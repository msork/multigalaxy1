"""Fit semantic Luigi walk features to the native Waluigi 38-bone model.

This Blender script reads source/native actions and the calibration JSON only.
It creates a disposable complete target character, bakes a new action, validates
it, and saves a separate preview .blend. Production rigs/actions are read-only.
"""
import argparse
import gzip
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze_native_arm_context import source_pose_from_curves
from build_context_pca_previews import move_to_collection
from convert_native_owner_actions import (
    basis_from_pose, duplicate_rig, evaluated_bone_matrices, make_action, set_frame,
)
from test_semantic_preview_noop import copy_action_slot, duplicate_mesh
from test_semantic_preview_noop import foot_positions, weighted_group_centroid
from semantic_arm_fit_math import mirror_pose_across_sagittal, semantic_features
from native_pose_graph import (
    build_native_pose_graph, solve_native_pose_path,
)
from context_dataset import normalize_context_dataset

TORSO_CONTEXT = {"bone_1", "bone_2", "bone_3", "bone_5", "bone_6",
                 "bone_19", "bone_20", "bone_21"}
SOURCE_PROFILE = {
    # Shoulder motion is the upper-arm orientation relative to torso; this
    # includes Luigi's clavicle-like ShoulderL parent transform.
    "torso": "Spine2", "shoulder": "ArmL1", "upper": "ArmL1",
    "elbow_a": "ArmL1", "elbow_b": "ArmL2", "forearm": "ArmL2",
    "forearm_overlap": "ArmL2", "wrist": "HandL", "hand": "HandL",
    "shoulder_point_bone": "ArmL1", "shoulder_endpoint": "head",
    "elbow_endpoint_a": "tail", "elbow_endpoint_b": "head",
    "forearm_endpoint": "tail", "overlap_endpoint": "tail",
    "wrist_endpoint": "head", "hand_endpoint": "tail",
}
TARGET_PROFILE = {
    "torso": "bone_21", "shoulder": "bone_23", "upper": "bone_23",
    "elbow_a": "bone_26", "elbow_b": "bone_29", "forearm": "bone_27",
    "forearm_overlap": "bone_29", "wrist": "bone_30", "hand": "bone_30",
    "shoulder_point_bone": "bone_23", "shoulder_endpoint": "head",
    "elbow_endpoint_a": "head", "elbow_endpoint_b": "head",
    "forearm_endpoint": "tail", "overlap_endpoint": "tail",
    "wrist_endpoint": "head", "hand_endpoint": "tail",
}
SOURCE_PROFILE_RIGHT = {
    "torso": "Spine2", "shoulder": "ArmR1", "upper": "ArmR1",
    "elbow_a": "ArmR1", "elbow_b": "ArmR2", "forearm": "ArmR2",
    "forearm_overlap": "ArmR2", "wrist": "HandR", "hand": "HandR",
    "shoulder_point_bone": "ArmR1", "shoulder_endpoint": "head",
    "elbow_endpoint_a": "tail", "elbow_endpoint_b": "head",
    "forearm_endpoint": "tail", "overlap_endpoint": "tail",
    "wrist_endpoint": "head", "hand_endpoint": "tail",
}
TARGET_PROFILE_RIGHT = {
    "torso": "bone_21", "shoulder": "bone_58", "upper": "bone_58",
    "elbow_a": "bone_61", "elbow_b": "bone_64", "forearm": "bone_62",
    "forearm_overlap": "bone_64", "wrist": "bone_65", "hand": "bone_65",
    "shoulder_point_bone": "bone_58", "shoulder_endpoint": "head",
    "elbow_endpoint_a": "head", "elbow_endpoint_b": "head",
    "forearm_endpoint": "tail", "overlap_endpoint": "tail",
    "wrist_endpoint": "head", "hand_endpoint": "tail",
}
LEFT_TO_RIGHT_BONES = {
    "bone_22": "bone_57", "bone_23": "bone_58", "bone_24": "bone_59",
    "bone_25": "bone_60", "bone_26": "bone_61", "bone_27": "bone_62",
    "bone_29": "bone_64", "bone_30": "bone_65",
    "bone_31": "bone_66", "bone_32": "bone_67", "bone_33": "bone_68",
    "bone_34": "bone_69", "bone_35": "bone_70", "bone_36": "bone_71",
    "bone_37": "bone_72", "bone_38": "bone_73", "bone_39": "bone_74",
    "bone_40": "bone_75", "bone_41": "bone_76", "bone_42": "bone_77",
    "bone_43": "bone_78", "bone_44": "bone_79", "bone_45": "bone_80",
    "bone_46": "bone_81", "bone_47": "bone_82", "bone_48": "bone_83",
    "bone_49": "bone_84", "bone_50": "bone_85", "bone_51": "bone_86",
}
RIGHT_GAP_PAIRS = {
    "shoulder": ("bone_21", "tail", "bone_57", "head"),
    "elbow_main": ("bone_59", "tail", "bone_61", "head"),
    "elbow_overlap": ("bone_60", "tail", "bone_64", "head"),
    "wrist_glove": ("bone_62", "tail", "bone_65", "head"),
}
SEMANTIC_NAMES = ("shoulder_rot_x", "shoulder_rot_y", "shoulder_rot_z",
                  "elbow_flexion", "forearm_roll", "wrist_rot_x",
                  "wrist_rot_y", "wrist_rot_z", "hand_radial",
                  "hand_tangential", "hand_depth")


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--source-rig", default="luigi.001_armature")
    parser.add_argument("--source-action", default="luigi.001_armature_walk")
    parser.add_argument("--source-slot", default="OBluigi.001_armature")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--baseline-rig", default="Waluigi_Arm_Test")
    parser.add_argument("--clean-rig", default="Waluigi_Arm_Clean_Backup")
    parser.add_argument("--baseline-mesh-prefix", default="ARMTEST_")
    parser.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--walk-slot", default="", help="optional explicit target walk action slot")
    parser.add_argument("--walk-curves-json", default="",
                        help="read-only curve export for a live walk action absent from the input .blend")
    parser.add_argument("--frames", nargs=2, type=int, default=(0, 60))
    parser.add_argument("--elbow-gain", type=float, default=1.5,
                        help="scale source frame-relative flexion excursion for target profile")
    parser.add_argument("--graph-candidates", type=int, default=128)
    parser.add_argument("--graph-semantic-neighbors", type=int, default=12)
    parser.add_argument("--graph-semantic-radius", type=float, default=1.5)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--action-name", default="Waluigi_LuigiWalk_SemanticBothArms_RawANM_v1")
    parser.add_argument("--preview-rig-name", default="SemanticWalk_Waluigi_Target_Rig")
    parser.add_argument("--preview-collection-name", default="SemanticWalk_Waluigi_LeftArm_Preview")
    parser.add_argument("--validated-noop-preview", default=os.path.join(
        HERE, "reports", "luigi_walk_semantic_waluigi_left_arm_noop.blend"),
        help="saved, visually validated no-op walk preview used as a geometry gate")
    parser.add_argument("--v1-report", default="tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_fit.json")
    parser.add_argument("--comparison-report", default="",
                        help="optional prior fit report for feature-by-feature residual deltas")
    return parser.parse_args(argv)


def matrix_rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def numpy_matrix(m):
    return np.asarray([[float(m[r][c]) for c in range(4)] for r in range(4)],
                      dtype=np.float64)


def object_transform_matrix(obj):
    """Return the authored root matrix when a saved preview has a stale world matrix.

    The validated workfile stores the unparented Waluigi rig with its intended
    43.437 transform in matrix_basis, while matrix_world can evaluate as identity
    after reopening its layered pose action. In that specific no-parent,
    no-object-constraint case, the authored basis is the rig's validated root.
    """
    world = obj.matrix_world.copy()
    basis = obj.matrix_basis.copy()
    if obj.parent is None and len(obj.constraints) == 0:
        if max(abs(float(world[r][c]) - float(basis[r][c]))
               for r in range(4) for c in range(4)) > 1e-4:
            return basis
    return world


def proper_rotation(matrix):
    a = np.asarray(matrix, dtype=np.float64)[:3, :3]
    u, _s, vt = np.linalg.svd(a)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def frame_map(source_obj, source_rest, source_bone,
              target_obj, target_rest, target_bone):
    source_frame = proper_rotation(object_transform_matrix(source_obj)) @ numpy_matrix(source_rest[source_bone]["rest_matrix"])[:3, :3]
    target_frame = proper_rotation(object_transform_matrix(target_obj)) @ numpy_matrix(target_rest[target_bone]["rest_matrix"])[:3, :3]
    return target_frame.T @ source_frame


def target_rest_from_dataset(dataset):
    return dataset["rest_bones"]


def precompute_weighted_mesh_centers(mesh_objects, bone_names):
    """Collect weighted rest-space mesh centroids for arm/context groups."""
    wanted = set(bone_names)
    accum = {}
    for obj in mesh_objects:
        mesh_to_armature = numpy_matrix(obj.matrix_basis)
        for group in obj.vertex_groups:
            if group.name not in wanted:
                continue
            weighted_sum = np.zeros(3, dtype=np.float64)
            total_weight = 0.0
            for vertex in obj.data.vertices:
                for assignment in vertex.groups:
                    if assignment.group != group.index or assignment.weight <= 0.0:
                        continue
                    point = mesh_to_armature @ np.r_[np.asarray(vertex.co, dtype=np.float64), 1.0]
                    weighted_sum += float(assignment.weight) * point[:3]
                    total_weight += float(assignment.weight)
            if total_weight > 0.0:
                row = accum.setdefault(group.name, {"weighted_sum": np.zeros(3), "weight": 0.0})
                row["weighted_sum"] += weighted_sum
                row["weight"] += total_weight
    return {name: {"center": (row["weighted_sum"] / row["weight"]).tolist(),
                   "weight": float(row["weight"])}
            for name, row in accum.items() if row["weight"] > 0.0}


def build_mesh_centroid_rest_cache(centers, rest, rest_all):
    right_names = set(LEFT_TO_RIGHT_BONES.values())
    return {name: np.linalg.inv(numpy_matrix(
                (rest_all if name in right_names else rest)[name]["rest_matrix"]))
            for name in centers}


def pose_sequence_jump_report(pose_rows, bones, rest_bones, frames,
                              character_length, named_pairs=((31, 33), (40, 43), (48, 51))):
    """Measure adjacent full-context SE(3) motion, including requested windows."""
    rest_inverse = {name: np.linalg.inv(numpy_matrix(rest_bones[name]["rest_matrix"]))
                    for name in bones}

    def pair_measure(a_row, b_row, frame_a, frame_b):
        maximum = {"normalized_se3": 0.0}
        squared = []
        for name in bones:
            a = rest_inverse[name] @ numpy_matrix(a_row[name])
            b = rest_inverse[name] @ numpy_matrix(b_row[name])
            relative_rotation = a[:3, :3].T @ b[:3, :3]
            angle = math.acos(float(np.clip((np.trace(relative_rotation) - 1.0) * 0.5,
                                            -1.0, 1.0)))
            translation = float(np.linalg.norm(b[:3, 3] - a[:3, 3]))
            normalized = math.sqrt(angle * angle + (translation / character_length) ** 2)
            squared.append(normalized * normalized)
            if normalized > maximum["normalized_se3"]:
                maximum = {"normalized_se3": normalized, "bone": name,
                           "rotation_radians": angle, "translation_armature_units": translation,
                           "frame_pair": [int(frame_a), int(frame_b)]}
        return {**maximum, "rms_normalized_se3": math.sqrt(float(np.mean(squared)))}

    adjacent = [pair_measure(pose_rows[i], pose_rows[i + 1], frames[i], frames[i + 1])
                for i in range(len(frames) - 1)]
    worst_index = max(range(len(adjacent)), key=lambda i: adjacent[i]["normalized_se3"])
    pair_windows = {}
    for start, end in named_pairs:
        if start not in frames or end not in frames:
            continue
        first, last = frames.index(start), frames.index(end)
        pair_windows[f"{start}->{end}"] = {
            "direct": pair_measure(pose_rows[first], pose_rows[last], start, end),
            "adjacent": adjacent[first:last]}
    return {"max_adjacent": adjacent[worst_index],
            "mean_adjacent_rms_normalized_se3": float(np.mean([row["rms_normalized_se3"] for row in adjacent])),
            "adjacent_pairs": adjacent,
            "requested_windows": pair_windows}


def source_rest_from_rig(rig):
    return {bone.name: {"parent": bone.parent.name if bone.parent else None,
                        "head": [float(v) for v in bone.head_local],
                        "tail": [float(v) for v in bone.tail_local],
                        "length": float(bone.length),
                        "rest_matrix": matrix_rows(bone.matrix_local)}
            for bone in rig.data.bones}


def pose_dict_from_sample(sample, bones):
    return {name: np.asarray(sample["bones"][name]["armature_pose"], dtype=np.float64)
            for name in bones}


def semantic_from_source_sample(rest, pose, body_map, forearm_map,
                                profile=SOURCE_PROFILE, forearm_axes_local=None,
                                elbow_absolute=False):
    return semantic_features(rest, pose, profile, body_map, forearm_map,
                             forearm_axes_local, elbow_absolute)


def semantic_from_target_sample(rest, pose, profile=TARGET_PROFILE,
                                forearm_frame_map=None, forearm_axes_local=None,
                                body_frame_map=None, elbow_absolute=False):
    return semantic_features(rest, pose, profile, body_frame_map,
                             forearm_frame_map, forearm_axes_local, elbow_absolute)


GAP_PAIRS = {"shoulder": ("bone_21", "tail", "bone_22", "head"),
             "elbow_main": ("bone_24", "tail", "bone_26", "head"),
             "elbow_overlap": ("bone_25", "tail", "bone_29", "head"),
             "wrist_glove": ("bone_27", "tail", "bone_30", "head")}


def context_joint_gaps(pose, rest_bones, gap_pairs=GAP_PAIRS):
    points = {}
    for _label, (a, ea, b, eb) in gap_pairs.items():
        for name, endpoint in ((a, ea), (b, eb)):
            key = (name, endpoint)
            if key in points:
                continue
            rest = numpy_matrix(rest_bones[name]["rest_matrix"])
            local_point = np.r_[rest_bones[name][endpoint], 1.0]
            points[key] = (_matrix_for_fit(pose[name]) @ np.linalg.inv(rest) @ local_point)[:3]
    return np.asarray([np.linalg.norm(points[(a, ea)] - points[(b, eb)])
                       for a, ea, b, eb in gap_pairs.values()], dtype=np.float64)


def _matrix_for_fit(value):
    return np.asarray(value, dtype=np.float64).reshape(4, 4)


def pose_quaternion_descriptor(pose, bone_names, rest_inverse, length_scale):
    rows = []
    for name in bone_names:
        relative = rest_inverse[name] @ numpy_matrix(pose[name])
        quaternion = Matrix(relative[:3, :3].tolist()).to_quaternion()
        quaternion.normalize()
        rows.append([*(relative[:3, 3] / length_scale).tolist(),
                     float(quaternion.w), float(quaternion.x),
                     float(quaternion.y), float(quaternion.z)])
    return np.asarray(rows, dtype=np.float64)


def compose_final_semantic_pose(base_pose, left_pose, right_pose, *,
                                target_rest, target_rest_all, arm_bones,
                                torso_context, semantic_arm_names,
                                left_to_right, right_subtree,
                                right_finger_names, parent_by_bone,
                                right_root_name, torso_name):
    """Compose exactly the pose used by the preview/bake path, in NumPy space."""
    mixed = {name: numpy_matrix(matrix).copy() for name, matrix in base_pose.items()}
    torso_from = numpy_matrix(left_pose[torso_name])
    torso_to = numpy_matrix(base_pose[torso_name])
    rebase = torso_to @ np.linalg.inv(torso_from)
    for name in arm_bones:
        mixed[name] = rebase @ numpy_matrix(left_pose[name])

    torso_rest = numpy_matrix(target_rest[torso_name]["rest_matrix"])
    for left_name in semantic_arm_names:
        right_name = left_to_right[left_name]
        mixed[right_name] = mirror_pose_across_sagittal(
            rebase @ numpy_matrix(right_pose[left_name]), torso_to, torso_rest,
            numpy_matrix(target_rest[left_name]["rest_matrix"]),
            numpy_matrix(target_rest_all[right_name]["rest_matrix"]))

    root_delta = (numpy_matrix(base_pose[right_root_name])[:3, 3] -
                  mixed[right_root_name][:3, 3])
    for name in right_subtree:
        mixed[name] = mixed[name].copy()
        mixed[name][:3, 3] += root_delta

    def hierarchy_depth(name):
        depth = 0
        parent = parent_by_bone.get(name)
        while parent is not None:
            depth += 1
            parent = parent_by_bone.get(parent)
        return depth

    for name in sorted(right_finger_names, key=hierarchy_depth):
        parent_name = parent_by_bone[name]
        relative = (np.linalg.inv(numpy_matrix(base_pose[parent_name])) @
                    numpy_matrix(base_pose[name]))
        mixed[name] = mixed[parent_name] @ relative
    for name in torso_context:
        mixed[name] = numpy_matrix(base_pose[name]).copy()
    return mixed, float(np.linalg.norm(root_delta))


def assign_pose_matrices_hierarchical(rig, pose_matrices):
    """Apply armature-space poses parent-first through Blender's matrix setter.

    Assigning every derived matrix_basis at once was not equivalent for this
    rig's overlapping/finger hierarchy: Blender evaluated the request with
    parent-context-dependent offsets. Set PoseBone.matrix at each hierarchy
    depth and flush the depsgraph before visiting children. Blender then
    computes the correct local basis in the active parent context.
    """
    depths = {}
    for name in pose_matrices:
        depth, bone = 0, rig.data.bones[name]
        while bone.parent is not None:
            depth += 1
            bone = bone.parent
        depths[name] = depth
    for depth in range(max(depths.values(), default=-1) + 1):
        for name in sorted(name for name, value in depths.items() if value == depth):
            rig.pose.bones[name].matrix = Matrix(pose_matrices[name])
        bpy.context.view_layer.update()
    return {name: rig.pose.bones[name].matrix_basis.copy()
            for name in pose_matrices}


def select_walk_slot(action, slot_id, bone_names):
    matches = [slot for slot in action.slots
               if (not slot_id or slot.identifier == slot_id)]
    if slot_id and len(matches) != 1:
        raise RuntimeError(f"walk action has no unique requested slot {slot_id}")
    if not matches:
        for slot in action.slots:
            bags = [bag for layer in action.layers for strip in layer.strips
                    for bag in strip.channelbags if bag.slot == slot]
            names = {curve.data_path.split('"')[1] for bag in bags for curve in bag.fcurves
                     if 'pose.bones["' in curve.data_path}
            if names & set(bone_names):
                matches.append(slot)
    if len(matches) != 1:
        raise RuntimeError("could not resolve a unique slot for validated Waluigi walk action")
    return matches[0]


def import_walk_curve_action(payload, target):
    """Recreate a local disposable copy of a baked action exported read-only."""
    action = bpy.data.actions.new("_MG1_Imported_" + payload["action"])
    action.use_fake_user = True
    slot = action.slots.new("OBJECT", target.name)
    layer = action.layers.new("Imported validated baked walk curves")
    strip = layer.strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    by_path = {}
    for curve in payload["curves"]:
        path = curve["data_path"]
        index = int(curve["array_index"])
        match = __import__("re").match(r'pose\.bones\["([^\"]+)"\]', path)
        group = match.group(1) if match else None
        fc = bag.fcurves.new(path, index=index, group_name=group)
        for frame, value in curve["keyframes"]:
            point = fc.keyframe_points.insert(float(frame), float(value), options={"FAST"})
            point.interpolation = "LINEAR"
        fc.update()
        by_path[(path, index)] = fc
    target.animation_data_create()
    target.animation_data.action = action
    target.animation_data.action_slot = slot
    return action, slot


def clear_constraints(rig):
    removed = []
    for constraint in list(rig.constraints):
        removed.append("object:" + constraint.name)
        rig.constraints.remove(constraint)
    for pose_bone in rig.pose.bones:
        for constraint in list(pose_bone.constraints):
            removed.append(pose_bone.name + ":" + constraint.name)
            pose_bone.constraints.remove(constraint)
    return removed


def enable_preview_collection(scene, view_layer, collection):
    """Make the generated collection visible in the saved active view layer."""
    collection.hide_viewport = False
    collection.hide_render = False

    def visit(layer_collection):
        if layer_collection.collection == collection:
            layer_collection.exclude = False
            layer_collection.hide_viewport = False
            return True
        for child in layer_collection.children:
            if visit(child):
                layer_collection.exclude = False
                layer_collection.hide_viewport = False
                return True
        return False

    if not visit(view_layer.layer_collection):
        raise RuntimeError("preview collection is not linked into the active view layer")
    if collection.name not in {child.name for child in scene.collection.children}:
        raise RuntimeError("preview collection is not linked into the active scene")


def mesh_vertices(mesh_objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    output = {}
    for obj in mesh_objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            output[obj.name] = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()
    return output


def append_noop_reference(filepath, collection_name):
    """Append the validated no-op rig and its full mesh for preview comparison."""
    if not os.path.isfile(filepath):
        raise RuntimeError("validated no-op preview is missing: " + filepath)
    with bpy.data.libraries.load(filepath, link=False) as (source, target):
        rig_names = [name for name in source.objects
                     if name == "ValidatedWalk_NoOp_Preview_Rig"]
        mesh_names = [name for name in source.objects
                      if name.startswith("ValidatedWalk_NoOp_Preview_ARMTEST_")]
        if len(rig_names) != 1 or len(mesh_names) != 2:
            raise RuntimeError("validated no-op preview does not contain its expected rig and two meshes")
        target.objects = rig_names + mesh_names
    objects = [obj for obj in target.objects if obj is not None]
    rig = next((obj for obj in objects if obj.type == "ARMATURE"), None)
    meshes = sorted((obj for obj in objects if obj.type == "MESH"), key=lambda obj: obj.name)
    if rig is None or len(meshes) != 2:
        raise RuntimeError("could not append the complete validated no-op reference")
    collection = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(collection)
    for obj in objects:
        collection.objects.link(obj)
    if rig.animation_data is None or rig.animation_data.action is None:
        raise RuntimeError("validated no-op reference rig has no baked walk action")
    for mesh in meshes:
        armature_modifiers = [modifier for modifier in mesh.modifiers
                              if modifier.type == "ARMATURE"]
        if (mesh.parent != rig or not armature_modifiers or
            any(modifier.object != rig for modifier in armature_modifiers)):
            raise RuntimeError("validated no-op reference mesh binding is invalid: " + mesh.name)
    return rig, meshes, collection


def preview_mesh_key(obj):
    # Blender adds .001 suffixes when a previous preview object is still in
    # the source scene. Topology is stable across those object-name changes.
    return (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons))


def compare_preview_meshes(actual_meshes, reference_meshes, frames, tolerance):
    actual_by_key = {preview_mesh_key(obj): obj for obj in actual_meshes}
    reference_by_key = {preview_mesh_key(obj): obj for obj in reference_meshes}
    if len(actual_by_key) != len(actual_meshes) or len(reference_by_key) != len(reference_meshes):
        raise RuntimeError("preview mesh topology keys are not unique")
    if actual_by_key.keys() != reference_by_key.keys():
        raise RuntimeError("preview and validated no-op mesh identities differ")
    max_error = {"error": 0.0}
    samples = 0
    per_frame = []
    for frame in frames:
        set_frame(bpy.context.scene, float(frame))
        actual = mesh_vertices(actual_meshes)
        reference = mesh_vertices(reference_meshes)
        frame_max = {"error": 0.0}
        for key in sorted(actual_by_key):
            left = actual[actual_by_key[key].name]
            right = reference[reference_by_key[key].name]
            if len(left) != len(right):
                raise RuntimeError(f"no-op mesh vertex count mismatch for {key}: {len(left)} vs {len(right)}")
            for index, (a, b) in enumerate(zip(left, right)):
                error = float((a - b).length)
                samples += 1
                if error > frame_max["error"]:
                    frame_max = {"error": error, "frame": int(frame),
                                 "mesh": key, "vertex": index}
                if error > max_error["error"]:
                    max_error = {"error": error, "frame": int(frame),
                                 "mesh": key, "vertex": index}
        per_frame.append(frame_max)
    report = {"max_world_vertex_error": max_error,
              "per_frame_max_world_vertex_error": per_frame,
              "vertex_samples": samples,
              "tolerance": float(tolerance),
              "gate": "pass" if max_error["error"] <= tolerance else "fail"}
    if report["gate"] != "pass":
        raise RuntimeError("complete walk baseline differs from validated no-op preview: " +
                           json.dumps(max_error, sort_keys=True))
    return report


def continuity(rig):
    ev = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    pb = ev.pose.bones
    pairs = {"shoulder": ("bone_21", "tail", "bone_22", "head"),
             "elbow_main": ("bone_24", "tail", "bone_26", "head"),
             "elbow_overlap": ("bone_25", "tail", "bone_29", "head"),
             "wrist_glove": ("bone_27", "tail", "bone_30", "head")}
    out = {}
    for name, (a, ea, b, eb) in pairs.items():
        out[name] = float((getattr(pb[a], ea) - getattr(pb[b], eb)).length)
    return out


def continuity_for_pairs(rig, pairs):
    ev = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    pb = ev.pose.bones
    return {name: float((getattr(pb[a], ea) - getattr(pb[b], eb)).length)
            for name, (a, ea, b, eb) in pairs.items()}


def descendant_bone_names(armature_data, root_name):
    result = set()
    for bone in armature_data.bones:
        parent = bone.parent
        while parent is not None:
            if parent.name == root_name:
                result.add(bone.name)
                break
            parent = parent.parent
    return result


def rebase_elbow_excursion(semantics, gain):
    mapped = np.asarray(semantics, dtype=np.float64).copy()
    reference = float(mapped[0, 3])
    mapped[:, 3] = (mapped[:, 3] - reference) * float(gain)
    return mapped, reference


def source_right_to_left_semantics(values, forearm_right_to_left, body_reflection):
    original = np.asarray(values, dtype=np.float64)
    result = np.atleast_2d(original.copy())
    torso_reflection = np.asarray(body_reflection, dtype=np.float64)
    angular_reflection = float(np.linalg.det(torso_reflection)) * torso_reflection
    forearm_map = np.asarray(forearm_right_to_left, dtype=np.float64)
    result[:, 0:3] = result[:, 0:3] @ angular_reflection.T
    roll_sign = 1.0 if float((forearm_map @ np.array((0.0, 1.0, 0.0)))[1]) >= 0 else -1.0
    result[:, 4] *= roll_sign
    result[:, 5:8] = result[:, 5:8] @ forearm_map.T
    result[:, 8:11] = result[:, 8:11] @ torso_reflection.T
    return result[0] if original.ndim == 1 else result


def native_continuity_ranges(actions):
    pairs = {"shoulder": ("bone_21", "tail_armature", "bone_22", "head_armature"),
             "elbow_main": ("bone_24", "tail_armature", "bone_26", "head_armature"),
             "elbow_overlap": ("bone_25", "tail_armature", "bone_29", "head_armature"),
             "wrist_glove": ("bone_27", "tail_armature", "bone_30", "head_armature")}
    values = {name: [] for name in pairs}
    for action in actions:
        for sample in action["samples"]:
            for label, (a, ea, b, eb) in pairs.items():
                pa = np.asarray(sample["bones"][a][ea], dtype=np.float64)
                pb = np.asarray(sample["bones"][b][eb], dtype=np.float64)
                values[label].append(float(np.linalg.norm(pa - pb)))
    return {name: {"min": float(min(rows)), "max": float(max(rows)),
                   "median": float(np.median(rows))} for name, rows in values.items()}


def finite_pose(rig):
    failures = []
    for pb in rig.pose.bones:
        for row in pb.matrix:
            if not all(math.isfinite(float(v)) for v in row):
                failures.append(pb.name + ":matrix")
                break
        if any(not math.isfinite(float(v)) for v in pb.scale):
            failures.append(pb.name + ":scale")
        if any(float(v) <= 0.0 for v in pb.scale):
            failures.append(pb.name + ":non_positive_scale")
    return failures


def main():
    args = args_parse()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = normalize_context_dataset(json.load(stream))
    bones = list(dataset["bones"])
    if len(bones) != 38 or len(dataset["training_actions"]) != dataset["training_action_count"]:
        raise RuntimeError("target input is not the authoritative 38-bone raw-ANM context dataset")
    source_rig = bpy.data.objects.get(args.source_rig)
    target_original = bpy.data.objects.get(args.target_rig)
    baseline_original = bpy.data.objects.get(args.baseline_rig)
    clean_original = bpy.data.objects.get(args.clean_rig)
    source_action = bpy.data.actions.get(args.source_action)
    walk_action = bpy.data.actions.get(args.walk_action)
    walk_curve_payload = None
    if walk_action is None and args.walk_curves_json:
        with open(args.walk_curves_json, "r", encoding="utf-8") as stream:
            walk_curve_payload = json.load(stream)
        if walk_curve_payload.get("action") != args.walk_action:
            raise RuntimeError("walk curve export action name differs from requested action")
    if (not source_rig or source_rig.type != "ARMATURE" or
        not target_original or target_original.type != "ARMATURE" or
        not baseline_original or baseline_original.type != "ARMATURE" or
        not clean_original or clean_original.type != "ARMATURE"):
        raise RuntimeError("source Luigi, production Waluigi, validated walk rig, or clean rig is missing")
    if source_action is None:
        raise RuntimeError("source walk action is missing: " + args.source_action)
    if walk_action is None and walk_curve_payload is None:
        raise RuntimeError("validated target walk action is missing: " + args.walk_action)
    source_slots = [slot for slot in source_action.slots if slot.identifier == args.source_slot]
    if len(source_slots) != 1:
        raise RuntimeError("source action slot is not unique: " + args.source_slot)
    source_slot = source_slots[0]
    walk_slot = (select_walk_slot(walk_action, args.walk_slot, target_original.data.bones.keys())
                 if walk_action is not None else None)
    source_profile_names = {SOURCE_PROFILE[key] for key in (
        "torso", "shoulder", "upper", "elbow_a", "elbow_b", "forearm",
        "forearm_overlap", "wrist", "hand", "shoulder_point_bone")}
    source_profile_names |= {SOURCE_PROFILE_RIGHT[key] for key in (
        "torso", "shoulder", "upper", "elbow_a", "elbow_b", "forearm",
        "forearm_overlap", "wrist", "hand", "shoulder_point_bone")}
    if not source_profile_names.issubset(set(source_rig.data.bones.keys())):
        raise RuntimeError("source Luigi profile bones are missing: " + repr(sorted(source_profile_names - set(source_rig.data.bones.keys()))))
    if not set(bones).issubset(set(target_original.data.bones.keys())):
        raise RuntimeError("target Waluigi is missing context profile bones")
    if abs(float(target_original.scale[0]) - 43.437) > 0.01:
        raise RuntimeError("production Waluigi scale changed; refusing to proceed")
    baseline_root_matrix = object_transform_matrix(baseline_original)
    if abs(float(baseline_root_matrix.to_scale()[0]) - 43.437) > 0.01:
        raise RuntimeError("validated walk rig does not preserve the intentional 43.437 transform")
    if set(clean_original.data.bones.keys()) != set(target_original.data.bones.keys()):
        raise RuntimeError("clean pose rig and production target have different bone sets")
    for bone in target_original.data.bones:
        if matrix_error(clean_original.data.bones[bone.name].matrix_local,
                        target_original.data.bones[bone.name].matrix_local) > 1e-6:
            raise RuntimeError("clean pose rig rest matrix differs from production: " + bone.name)
    start, end = args.frames
    if (start, end) != (0, 60) or tuple(float(v) for v in source_action.frame_range) != (0.0, 60.0):
        raise RuntimeError("this prototype is limited to the Luigi walk's frames 0–60")
    frames = list(range(start, end + 1))

    target_rest = target_rest_from_dataset(dataset)
    target_rest_all = source_rest_from_rig(target_original)
    source_rest = source_rest_from_rig(source_rig)
    body_map = frame_map(source_rig, source_rest, SOURCE_PROFILE["torso"],
                         target_original, target_rest, TARGET_PROFILE["torso"])
    forearm_map = frame_map(source_rig, source_rest, SOURCE_PROFILE["forearm"],
                            target_original, target_rest, TARGET_PROFILE["forearm"])
    body_map_right = frame_map(source_rig, source_rest, SOURCE_PROFILE_RIGHT["torso"],
                               target_original, target_rest_all, TARGET_PROFILE_RIGHT["torso"])
    forearm_map_right = frame_map(source_rig, source_rest, SOURCE_PROFILE_RIGHT["forearm"],
                                  target_original, target_rest_all, TARGET_PROFILE_RIGHT["forearm"])
    left_forearm_rotation = proper_rotation(numpy_matrix(
        target_rest_all[TARGET_PROFILE["forearm"]]["rest_matrix"]))
    right_forearm_rotation = proper_rotation(numpy_matrix(
        target_rest_all[TARGET_PROFILE_RIGHT["forearm"]]["rest_matrix"]))
    right_forearm_to_left = left_forearm_rotation.T @ right_forearm_rotation
    torso_rotation = proper_rotation(numpy_matrix(
        target_rest_all[TARGET_PROFILE["torso"]]["rest_matrix"]))
    armature_reflection = np.diag((-1.0, 1.0, 1.0))
    body_reflection = torso_rotation.T @ armature_reflection @ torso_rotation

    target_actions = [row for row in dataset["actions"] if row["split"] == "train"]
    target_holdouts = [row for row in dataset["actions"] if row["split"] == "holdout"]
    native_gap_ranges = native_continuity_ranges(target_actions)
    native_gap_targets = np.median(np.asarray([
        context_joint_gaps(pose_dict_from_sample(sample, bones), target_rest)
        for row in target_actions for sample in row["samples"]], dtype=np.float64), axis=0)
    s_by_action = {}
    for action_row in dataset["actions"]:
        rest = target_rest
        s_by_action[action_row["action"]] = np.asarray([
            semantic_from_target_sample(rest, pose_dict_from_sample(sample, bones))
            for sample in action_row["samples"]], dtype=np.float64)
    training_sample_count = sum(len(row["samples"]) for row in target_actions)
    train_sample_action_names = [row["action"] for row in target_actions
                                 for _sample in row["samples"]]
    sem_train = np.concatenate([s_by_action[row["action"]] for row in target_actions])
    sem_mean = sem_train.mean(axis=0)
    sem_std = np.maximum(sem_train.std(axis=0), 1e-5)
    sem_train_norm = (sem_train - sem_mean) / sem_std

    scene = bpy.context.scene
    mesh_sources = sorted((obj for obj in bpy.data.objects
        if obj.type == "MESH" and obj.name.startswith(args.baseline_mesh_prefix) and
        any(mod.type == "ARMATURE" and mod.object == baseline_original
            for mod in obj.modifiers)), key=lambda obj: obj.name)
    if not mesh_sources:
        raise RuntimeError("validated walk has no complete ARMTEST meshes bound to its rig")
    mesh_centers = precompute_weighted_mesh_centers(
        mesh_sources, ({f"bone_{i}" for i in range(22, 53)} |
                       set(LEFT_TO_RIGHT_BONES.values())))
    if not any(name in mesh_centers for name in ("bone_27", "bone_30", "bone_62", "bone_65")):
        raise RuntimeError("weighted arm mesh centroids are unavailable for wrist/glove groups")
    upper_arm_rest = target_rest["bone_25"]
    upper_arm_length = upper_arm_rest.get("length")
    if upper_arm_length is None:
        upper_arm_length = np.linalg.norm(
            np.asarray(upper_arm_rest["tail"], dtype=np.float64) -
            np.asarray(upper_arm_rest["head"], dtype=np.float64))
    armature_length_scale = max(float(upper_arm_length), 1e-6)
    mesh_centroid_rest_inverse = build_mesh_centroid_rest_cache(
        mesh_centers, target_rest, target_rest_all)
    if walk_action is None:
        raise RuntimeError("joint composition fit requires the validated baked walk action")
    baseline_eval = duplicate_rig(clean_original, "_MG1_FinalComposition_BaselineEval")
    baseline_eval.matrix_world = baseline_root_matrix.copy()
    baseline_eval.hide_viewport = False
    baseline_eval.hide_render = False
    baseline_eval.hide_set(False)
    clear_constraints(baseline_eval)
    baseline_eval.animation_data_create()
    baseline_eval.animation_data.action = walk_action
    baseline_eval.animation_data.action_slot = walk_slot
    scene = bpy.context.scene
    baseline_frame = scene.frame_current
    baseline_subframe = scene.frame_subframe
    baseline_pose_rows = []
    try:
        for frame in frames:
            set_frame(scene, float(frame))
            baseline_pose_rows.append({name: numpy_matrix(matrix)
                for name, matrix in evaluated_bone_matrices(baseline_eval).items()})
    finally:
        scene.frame_set(baseline_frame, subframe=baseline_subframe)
        baseline_armature_data = baseline_eval.data
        bpy.data.objects.remove(baseline_eval, do_unlink=True)
        if baseline_armature_data.users == 0:
            bpy.data.armatures.remove(baseline_armature_data)
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    old_objects = set(bpy.data.objects)
    old_actions = set(bpy.data.actions)
    old_collections = set(bpy.data.collections)
    old_obj_visibility = {obj: obj.hide_get() for obj in old_objects}
    old_obj_hide_viewport = {obj: obj.hide_viewport for obj in old_objects}
    old_obj_hide_render = {obj: obj.hide_render for obj in old_objects}
    created_objects, created_meshes, created_actions = [], [], []
    target_copy = None
    collections = []
    try:
        target_copy = duplicate_rig(clean_original, args.preview_rig_name)
        target_copy.matrix_world = baseline_root_matrix.copy()
        # The clean source rig is intentionally hidden in the workfile.
        # Duplicates inherit both object-level and per-view-layer hiding;
        # Blender's depsgraph then returned stale/incomplete pose matrices
        # during fitting even though the rig was unhidden before saving.
        # Clear every visibility layer before the first walk sample or pose
        # write so the fitted matrices and the later baked action share the
        # same evaluated context.
        target_copy.hide_viewport = False
        target_copy.hide_render = False
        target_copy.hide_set(False)
        bpy.context.view_layer.update()
        if target_copy.hide_viewport or target_copy.hide_render or target_copy.hide_get():
            raise RuntimeError("disposable target rig could not be made evaluable before fitting")
        created_objects.append(target_copy)
        cleared_constraints = clear_constraints(target_copy)
        if walk_action is None:
            walk_action, walk_slot = import_walk_curve_action(walk_curve_payload, target_copy)
            created_actions.append(walk_action)
        elif walk_slot is None:
            walk_slot = select_walk_slot(walk_action, args.walk_slot,
                                         target_original.data.bones.keys())
        walk_source_action, walk_source_slot = walk_action, walk_slot
        walk_action = copy_action_slot(walk_source_action, walk_source_slot,
            target_copy, "_MG1_ValidatedWalk_CleanBasis")
        walk_action.name = "_MG1_ValidatedWalk_CleanBasis"
        walk_slot = walk_action.slots[0]
        created_actions.append(walk_action)
        source_semantics = []
        source_semantics_right = []
        source_pose_rows = []
        for frame in frames:
            set_frame(scene, float(frame))
            pose, _basis = source_pose_from_curves(source_rig, source_action,
                                                   source_slot, float(frame))
            pose_np = {name: numpy_matrix(matrix) for name, matrix in pose.items()}
            source_pose_rows.append(pose_np)
            source_semantics.append(semantic_from_source_sample(source_rest, pose_np,
                                                                  body_map, forearm_map))
            right_sem = semantic_from_source_sample(source_rest, pose_np,
                body_map_right, forearm_map_right, SOURCE_PROFILE_RIGHT)
            source_semantics_right.append(source_right_to_left_semantics(
                right_sem, right_forearm_to_left, body_reflection))
        source_semantics = np.asarray(source_semantics, dtype=np.float64)
        raw_source_semantics = source_semantics.copy()
        # Flexion is an excursion from the source walk's starting bend, not an
        # absolute angle relative to unlike Luigi/Waluigi rest poses. This
        # preserves the walk's signed per-frame bend while aligning its neutral
        # frame with the target rest-relative zero.
        source_elbow_reference = float(source_semantics[0, 3])
        source_semantics[:, 3] -= source_elbow_reference
        source_semantics[:, 3] *= args.elbow_gain
        raw_source_semantics_right = np.asarray(source_semantics_right, dtype=np.float64)
        source_semantics_right, source_elbow_reference_right = rebase_elbow_excursion(
            raw_source_semantics_right, args.elbow_gain)
        source_norm = (source_semantics - sem_mean) / sem_std
        source_norm_right = (source_semantics_right - sem_mean) / sem_std
        source_graph_semantics = np.c_[source_norm, source_norm_right]
        train_distance = np.linalg.norm(
            sem_train_norm[:, None, :] - source_norm[None, :, :], axis=2)
        distance_right = np.linalg.norm(
            sem_train_norm[:, None, :] - source_norm_right[None, :, :], axis=2)
        train_distance = np.linalg.norm(
            sem_train_norm[:, None, :] - source_norm[None, :, :], axis=2)
        distance_right = np.linalg.norm(
            sem_train_norm[:, None, :] - source_norm_right[None, :, :], axis=2)
        nearest_indices = np.argmin(train_distance, axis=0)
        nearest_indices_right = np.argmin(distance_right, axis=0)

        semantic_arm_names = {"bone_22", "bone_23", "bone_24", "bone_25",
                              "bone_26", "bone_27", "bone_29", "bone_30"}
        arm_bones = set(bones) - TORSO_CONTEXT
        right_arm_bones = {LEFT_TO_RIGHT_BONES[name] for name in semantic_arm_names}
        right_subtree = descendant_bone_names(target_original.data, "bone_57") | {"bone_57"}
        right_finger_names = right_subtree - right_arm_bones - {"bone_57"}
        parent_by_bone = {bone.name: bone.parent.name if bone.parent else None
                          for bone in target_original.data.bones}
        full_pose_bones = sorted(set(bones) | {
            LEFT_TO_RIGHT_BONES[name] for name in bones if name in LEFT_TO_RIGHT_BONES})
        full_pose_rest_inverse = {name: np.linalg.inv(
            numpy_matrix(target_rest_all[name]["rest_matrix"])) for name in full_pose_bones}
        left_regions = [
            [f"bone_{i}" for i in range(22, 52)], ["bone_22", "bone_23"],
            ["bone_25"], ["bone_24", "bone_26", "bone_29"], ["bone_27"],
            [f"bone_{i}" for i in range(30, 52)]]
        right_regions = [
            [LEFT_TO_RIGHT_BONES[name] for name in (
                "bone_22", "bone_23", "bone_24", "bone_25",
                "bone_26", "bone_27", "bone_29", "bone_30")],
            ["bone_57", "bone_58"], ["bone_60"], ["bone_59", "bone_61", "bone_64"],
            ["bone_62"], ["bone_65"]]
        sem_count = len(SEMANTIC_NAMES)
        left_gap_scales = right_gap_scales = (0.001, 0.0015, 0.0015, 0.0015)
        native_pose_rows, native_semantics, native_action_names = [], [], []
        native_sample_indices, native_times, native_normalized_times = [], [], []
        action_groups = {}
        for action_row in target_actions:
            action_name = action_row["action"]
            action_ids = []
            for sample_index, sample in enumerate(action_row["samples"]):
                node_id = len(native_pose_rows)
                action_ids.append(node_id)
                pose = pose_dict_from_sample(sample, bones)
                native_pose_rows.append(np.asarray([pose[name] for name in bones], dtype=np.float64))
                native_semantics.append(s_by_action[action_name][sample_index])
                native_action_names.append(action_name)
                native_sample_indices.append(sample_index)
                native_times.append(float(sample["time"]))
                native_normalized_times.append(float(sample["normalized_time"]))
            action_groups[action_name] = action_ids
        native_pose_rows = np.asarray(native_pose_rows, dtype=np.float64)
        native_semantics = np.asarray(native_semantics, dtype=np.float64)
        native_semantics_norm = (native_semantics - sem_mean) / sem_std
        native_graph_semantics = np.c_[native_semantics_norm, native_semantics_norm]
        left_rest_inverse = {name: np.linalg.inv(
            numpy_matrix(target_rest[name]["rest_matrix"])) for name in bones}
        native_pose_descriptors = np.asarray([
            pose_quaternion_descriptor(
                {name: row[index] for index, name in enumerate(bones)}, bones,
                left_rest_inverse, armature_length_scale)
            for row in native_pose_rows], dtype=np.float64)
        native_graph = build_native_pose_graph(
            native_graph_semantics, native_pose_descriptors, action_groups,
            neighbor_count=args.graph_semantic_neighbors,
            semantic_radius=args.graph_semantic_radius, pose_jump_limit=0.5)
        temporal_previous = {right: left for left, right in
                             native_graph["temporal_next"].items()}

        candidate_ids_by_frame, candidate_costs_by_frame = [], []
        candidate_semantics_by_frame, candidate_gaps_by_frame = [], []
        candidate_pose_descriptors_by_frame, candidate_root_deltas = [], []
        candidate_indices_by_frame = []
        candidate_count = max(1, min(args.graph_candidates, len(native_pose_rows)))
        for frame_index in range(len(frames)):
            distances = np.linalg.norm(
                native_graph_semantics - source_graph_semantics[frame_index][None, :], axis=1)
            order = np.argsort(distances)[:candidate_count]
            # Semantic nearest-neighbor ranking alone can omit the next keyed
            # samples of every promising native sequence. Seed the layer with
            # those local temporal neighborhoods so DP can follow real motion
            # instead of holding one pose because its continuation was absent.
            expanded = set(int(node_id) for node_id in order)
            for seed in order[:min(12, len(order))]:
                seed = int(seed)
                for direction in (native_graph["temporal_next"], temporal_previous):
                    cursor = seed
                    for _step in range(2):
                        cursor = direction.get(cursor)
                        if cursor is None:
                            break
                        expanded.add(int(cursor))
                nearby = sorted(native_graph["semantic_neighbors"][seed],
                    key=lambda neighbor: np.linalg.norm(
                        native_graph_semantics[neighbor] - native_graph_semantics[seed]))
                for neighbor in nearby[:2]:
                    expanded.add(int(neighbor))
            order = sorted(expanded, key=lambda node_id: (distances[node_id], node_id))
            valid_ids, costs, sem_rows, gaps_rows, descriptors, root_deltas = [], [], [], [], [], []
            for node_id in order:
                native_pose = {name: native_pose_rows[node_id][index]
                               for index, name in enumerate(bones)}
                final_pose, root_delta = compose_final_semantic_pose(
                    baseline_pose_rows[frame_index], native_pose, native_pose,
                    target_rest=target_rest, target_rest_all=target_rest_all,
                    arm_bones=arm_bones, torso_context=TORSO_CONTEXT,
                    semantic_arm_names=semantic_arm_names,
                    left_to_right=LEFT_TO_RIGHT_BONES, right_subtree=right_subtree,
                    right_finger_names=right_finger_names, parent_by_bone=parent_by_bone,
                    right_root_name="bone_57", torso_name=TARGET_PROFILE["torso"])
                left_sem = semantic_from_target_sample(
                    target_rest, {name: final_pose[name] for name in bones})
                right_sem_native = semantic_from_target_sample(
                    target_rest_all, final_pose, TARGET_PROFILE_RIGHT, right_forearm_to_left)
                right_sem = source_right_to_left_semantics(
                    right_sem_native, right_forearm_to_left, body_reflection)
                left_gap = context_joint_gaps(final_pose, target_rest, GAP_PAIRS)
                right_gap = context_joint_gaps(final_pose, target_rest_all, RIGHT_GAP_PAIRS)
                gaps = np.r_[left_gap, right_gap]
                if float(np.max(gaps[[3, 7]])) >= 0.02:
                    continue
                semantics = np.r_[(left_sem - sem_mean) / sem_std,
                                  (right_sem - sem_mean) / sem_std]
                semantic_target = source_graph_semantics[frame_index]
                semantic_cost = float(np.mean((semantics - semantic_target) ** 2))
                gap_cost = float(np.mean((gaps[[3, 7]] / 0.02) ** 2))
                valid_ids.append(int(node_id))
                # Sum the standardized semantic residuals (a squared
                # Mahalanobis distance), rather than averaging 22 dimensions;
                # averaging made a static state cheaper than tracking motion.
                costs.append(22.0 * semantic_cost + 2.0 * gap_cost)
                sem_rows.append(semantics)
                gaps_rows.append(gaps)
                descriptors.append(pose_quaternion_descriptor(
                    final_pose, full_pose_bones, full_pose_rest_inverse,
                    armature_length_scale))
                root_deltas.append(root_delta)
            if not valid_ids:
                raise RuntimeError(f"no native pose with attached gloves is available at frame {frames[frame_index]}")
            candidate_ids_by_frame.append(np.asarray(valid_ids, dtype=np.int64))
            candidate_costs_by_frame.append(np.asarray(costs, dtype=np.float64))
            candidate_semantics_by_frame.append(np.asarray(sem_rows, dtype=np.float64))
            candidate_gaps_by_frame.append(np.asarray(gaps_rows, dtype=np.float64))
            candidate_pose_descriptors_by_frame.append(np.asarray(descriptors, dtype=np.float64))
            candidate_root_deltas.append(np.asarray(root_deltas, dtype=np.float64))
            candidate_indices_by_frame.append(np.arange(len(valid_ids), dtype=np.int64))

        graph_path = solve_native_pose_path(
            candidate_ids_by_frame, candidate_costs_by_frame,
            candidate_semantics_by_frame, candidate_pose_descriptors_by_frame,
            source_graph_semantics, native_action_names, native_normalized_times,
            native_graph, max_pose_jump=0.5)
        selected_node_ids = graph_path["node_ids"]
        selected_layer_indices = graph_path["layer_indices"]
        final_pose_rows, right_root_anchor_corrections = [], []
        path_semantic_rows, path_gap_rows = [], []
        for frame_index, node_id in enumerate(selected_node_ids):
            native_pose = {name: native_pose_rows[node_id][index]
                           for index, name in enumerate(bones)}
            final_pose, root_delta = compose_final_semantic_pose(
                baseline_pose_rows[frame_index], native_pose, native_pose,
                target_rest=target_rest, target_rest_all=target_rest_all,
                arm_bones=arm_bones, torso_context=TORSO_CONTEXT,
                semantic_arm_names=semantic_arm_names,
                left_to_right=LEFT_TO_RIGHT_BONES, right_subtree=right_subtree,
                right_finger_names=right_finger_names, parent_by_bone=parent_by_bone,
                right_root_name="bone_57", torso_name=TARGET_PROFILE["torso"])
            final_pose_rows.append(final_pose)
            right_root_anchor_corrections.append(root_delta)
            layer_index = selected_layer_indices[frame_index]
            path_semantic_rows.append(candidate_semantics_by_frame[frame_index][layer_index])
            path_gap_rows.append(candidate_gaps_by_frame[frame_index][layer_index])
        path_semantic_rows = np.asarray(path_semantic_rows, dtype=np.float64)
        path_gap_rows = np.asarray(path_gap_rows, dtype=np.float64)
        nearest_graph_indices = [int(ids[0]) for ids in candidate_ids_by_frame]
        nearest_indices = np.asarray(nearest_graph_indices, dtype=np.int64)
        nearest_indices_right = nearest_indices.copy()
        train_sample_action_names = native_action_names
        nearest_prior = nearest_indices[:, None].astype(np.float64)
        nearest_right = nearest_prior.copy()
        proposal = nearest_prior.copy()
        proposal_right = nearest_right.copy()
        smoothed_initial = nearest_prior.copy()
        smoothed_right_initial = nearest_right.copy()
        smoothed = np.asarray(selected_node_ids, dtype=np.float64)[:, None]
        smoothed_right = smoothed.copy()
        semantic_inverse_costs = np.asarray([
            candidate_costs_by_frame[t][selected_layer_indices[t]]
            for t in range(len(frames))], dtype=np.float64)
        inverse_costs_right = semantic_inverse_costs.copy()
        left_joint_history = right_joint_history = [{
            "path_cost": graph_path["total_cost"],
            "temporal_transitions": graph_path["temporal_transition_count"],
            "neighbor_transitions": graph_path["neighbor_transition_count"],
            "hold_transitions": graph_path["hold_transition_count"]}]
        left_joint_outputs = path_semantic_rows[:, :sem_count]
        right_joint_outputs = path_semantic_rows[:, sem_count:]
        pca_pose_rows = [{name: native_pose_rows[node_id][index]
                          for index, name in enumerate(bones)}
                         for node_id in selected_node_ids]
        pca_pose_rows_right = pca_pose_rows
        native_pose_step_limit = 0.5

        preview_collection = bpy.data.collections.new(args.preview_collection_name)
        bpy.context.scene.collection.children.link(preview_collection)
        collections.append(preview_collection)
        move_to_collection(target_copy, preview_collection)
        target_meshes = [duplicate_mesh(obj, target_copy, preview_collection,
            "SemanticWalk_Waluigi_") for obj in mesh_sources]
        created_objects.extend(target_meshes)
        created_meshes.extend(target_meshes)
        noop_rig, noop_meshes, noop_collection = append_noop_reference(
            os.path.abspath(args.validated_noop_preview),
            "_MG1_Temporary_Validated_NoOp_Reference")
        collections.append(noop_collection)

        target_copy.animation_data_create()
        target_copy.animation_data.action = walk_action
        target_copy.animation_data.action_slot = walk_slot
        # Snapshot the validated walk completely before manual matrix writes.
        # Leaving its action active would let animated channels overwrite
        # matrix_basis assignments during dependency-graph evaluation.
        walk_pose_rows, walk_scale_rows, baseline_gaps = [], [], []
        baseline_gaps_right = []
        walk_foot_rows, walk_cap_rows = [], []
        for frame in frames:
            set_frame(scene, float(frame))
            walk_pose_rows.append({name: matrix.copy()
                                   for name, matrix in evaluated_bone_matrices(target_copy).items()})
            walk_scale_rows.append({pb.name: np.asarray(tuple(pb.scale), dtype=np.float64)
                                    for pb in target_copy.pose.bones})
            baseline_gaps.append(continuity(target_copy))
            baseline_gaps_right.append(continuity_for_pairs(target_copy, RIGHT_GAP_PAIRS))
            walk_foot_rows.append(foot_positions(target_copy, ("bone_8", "bone_14")))
            baseline_vertices = mesh_vertices(target_meshes)
            walk_cap_rows.append(weighted_group_centroid(target_meshes,
                [baseline_vertices[obj.name] for obj in target_meshes], "bone_53"))
        # This is deliberately checked before any semantic arm pose is applied.
        # It catches object-parent/bind errors that pose-matrix validation alone
        # cannot see.
        baseline_geometry_check = compare_preview_meshes(
            target_meshes, noop_meshes, frames, tolerance=1e-4)
        baseline_geometry_check["reference_file"] = os.path.abspath(args.validated_noop_preview)
        baseline_geometry_check["preview_mesh_bindings"] = [{
            "mesh": mesh.name,
            "parent": mesh.parent.name if mesh.parent else None,
            "object_scale": [float(v) for v in mesh.scale],
            "parent_inverse": matrix_rows(mesh.matrix_parent_inverse),
            "matrix_basis": matrix_rows(mesh.matrix_basis),
            "armature_targets": [modifier.object.name if modifier.object else None
                                 for modifier in mesh.modifiers if modifier.type == "ARMATURE"]}
            for mesh in target_meshes]
        target_copy.animation_data.action = None

        # The reference is temporary validation context, never part of the
        # delivered preview. Remove it after its complete 0..60 comparison.
        noop_action = noop_rig.animation_data.action if noop_rig.animation_data else None
        noop_data = [obj.data for obj in [noop_rig] + noop_meshes]
        for obj in [noop_rig] + noop_meshes:
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in noop_data:
            if data and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Armature):
                    bpy.data.armatures.remove(data)
        if noop_action and noop_action not in old_actions and noop_action.users == 0:
            bpy.data.actions.remove(noop_action, do_unlink=True)
        if noop_collection.users == 0:
            bpy.data.collections.remove(noop_collection)
        else:
            scene.collection.children.unlink(noop_collection)
            bpy.data.collections.remove(noop_collection)
        collections.remove(noop_collection)

        combined_pose_rows = []
        intended_pose_rows = []
        basis_samples = []
        combined_gaps = []
        combined_gaps_right = []
        max_pose_scale_delta_vs_walk = 0.0
        semantic_target_rows = []
        semantic_target_right_rows = []
        mesh_finite_failures = []
        arm_bones = set(bones) - TORSO_CONTEXT
        right_arm_bones = {LEFT_TO_RIGHT_BONES[name] for name in semantic_arm_names}
        right_subtree = descendant_bone_names(target_copy.data, "bone_57") | {"bone_57"}
        written_arm_bones = arm_bones | right_subtree
        right_finger_names = right_subtree - right_arm_bones - {"bone_57"}
        max_right_finger_local_error = 0.0
        max_pose_write_error = 0.0
        max_pose_write_detail = None
        pose_write_error_rows = []
        max_foot_baseline_error = 0.0
        max_cap_baseline_error = 0.0
        max_nonmodel_pose_error = 0.0
        max_composition_baseline_pose_error = 0.0
        for index, frame in enumerate(frames):
            set_frame(scene, float(frame))
            base_pose = walk_pose_rows[index]
            base_scales = walk_scale_rows[index]
            max_composition_baseline_pose_error = max(
                max_composition_baseline_pose_error,
                max(matrix_error(base_pose[name], baseline_pose_rows[index][name])
                    for name in base_pose))
            mixed_pose = {name: Matrix(matrix.tolist())
                          for name, matrix in final_pose_rows[index].items()}
            bases = assign_pose_matrices_hierarchical(target_copy, mixed_pose)
            actual = evaluated_bone_matrices(target_copy)
            for finger_name in right_finger_names:
                parent_name = target_copy.data.bones[finger_name].parent.name
                actual_relative = np.linalg.inv(numpy_matrix(actual[parent_name])) @ numpy_matrix(actual[finger_name])
                baseline_relative = (np.linalg.inv(numpy_matrix(base_pose[parent_name])) @
                                     numpy_matrix(base_pose[finger_name]))
                max_right_finger_local_error = max(max_right_finger_local_error,
                    matrix_error(actual_relative, baseline_relative))
            for foot_name, baseline_position in walk_foot_rows[index].items():
                current_position = foot_positions(target_copy, (foot_name,))[foot_name]
                max_foot_baseline_error = max(max_foot_baseline_error,
                    float((Vector(current_position) - Vector(baseline_position)).length))
            current_vertices = mesh_vertices(target_meshes)
            current_centroid = weighted_group_centroid(target_meshes,
                [current_vertices[obj.name] for obj in target_meshes], "bone_53")
            base_centroid = walk_cap_rows[index]
            if base_centroid is not None and current_centroid is not None:
                max_cap_baseline_error = max(max_cap_baseline_error,
                    float((Vector(base_centroid) - Vector(current_centroid)).length))
            max_pose_scale_delta_vs_walk = max(max_pose_scale_delta_vs_walk,
                max(float(np.max(np.abs(np.asarray(tuple(pb.scale), dtype=np.float64) -
                                         base_scales[pb.name])))
                    for pb in target_copy.pose.bones))
            for name in actual:
                if name not in written_arm_bones:
                    max_nonmodel_pose_error = max(max_nonmodel_pose_error,
                        matrix_error(actual[name], base_pose[name]))
                error = matrix_error(actual[name], mixed_pose[name])
                pose_write_error_rows.append({"bone": name, "frame": frame,
                    "error": error, "parent": target_copy.data.bones[name].parent.name
                        if target_copy.data.bones[name].parent else None})
                if error > max_pose_write_error:
                    max_pose_write_error = error
                    max_pose_write_detail = {"frame": frame, "bone": name,
                        "expected": matrix_rows(mixed_pose[name]),
                        "actual": matrix_rows(actual[name])}
            combined_pose_rows.append({name: matrix.copy() for name, matrix in actual.items()})
            intended_pose_rows.append({name: matrix.copy() for name, matrix in mixed_pose.items()})
            combined_gaps.append(continuity(target_copy))
            combined_gaps_right.append(continuity_for_pairs(target_copy, RIGHT_GAP_PAIRS))
            target_semantics = semantic_from_target_sample(target_rest,
                {name: numpy_matrix(actual[name]) for name in bones})
            semantic_target_rows.append(target_semantics)
            target_right_semantics = semantic_from_target_sample(
                target_rest_all, {name: numpy_matrix(actual[name])
                                  for name in target_rest_all},
                TARGET_PROFILE_RIGHT, right_forearm_to_left)
            semantic_target_right_rows.append(source_right_to_left_semantics(
                target_right_semantics, right_forearm_to_left, body_reflection))
            failures = finite_pose(target_copy)
            if failures:
                mesh_finite_failures.extend({"frame": frame, "bone": item} for item in failures)
            for mesh_name, vertices in mesh_vertices(target_meshes).items():
                for vertex_index, point in enumerate(vertices):
                    if not all(math.isfinite(float(v)) for v in point):
                        mesh_finite_failures.append({"frame": frame, "mesh": mesh_name,
                                                     "vertex": vertex_index})
            # Bake the evaluated pose actually produced by Blender, not the
            # requested armature-space matrices. Some Waluigi branch/finger
            # bones do not realize those requests exactly under their parent
            # context. The semantic report is computed from `actual`; deriving
            # local bases from that same evaluated pose makes the saved action
            # reproduce the measured fit rather than silently switching back
            # to the pre-evaluation request during save/reload.
            evaluated_bases = basis_from_pose(target_copy, actual)
            basis_samples.append((float(frame), evaluated_bases))

        action_name = args.action_name
        if bpy.data.actions.get(action_name):
            action_name += "_Prototype"
        baked_action = make_action(target_copy, action_name, basis_samples)
        created_actions.append(baked_action)
        target_copy.animation_data.action = baked_action
        target_copy.animation_data.action_slot = baked_action.slots[0]
        max_baked_replay_error = 0.0
        max_baked_intended_error = 0.0
        for index, frame in enumerate(frames):
            set_frame(scene, float(frame))
            baked_pose = evaluated_bone_matrices(target_copy)
            max_baked_replay_error = max(max_baked_replay_error,
                max(matrix_error(baked_pose[name], combined_pose_rows[index][name])
                    for name in baked_pose))
            max_baked_intended_error = max(max_baked_intended_error,
                max(matrix_error(baked_pose[name], intended_pose_rows[index][name])
                    for name in baked_pose))
            failures = finite_pose(target_copy)
            if failures:
                mesh_finite_failures.extend({"frame": frame, "bone": item} for item in failures)
            for mesh_name, vertices in mesh_vertices(target_meshes).items():
                for vertex_index, point in enumerate(vertices):
                    if not all(math.isfinite(float(v)) for v in point):
                        mesh_finite_failures.append({"frame": frame, "mesh": mesh_name,
                                                     "vertex": vertex_index})
        scene.frame_set(0)
        bpy.context.view_layer.update()

        baseline_gap_change = {key: max(abs(baseline_gaps[i][key] - combined_gaps[i][key])
                                        for i in range(len(frames))
                                        ) for key in baseline_gaps[0]}
        native_gap_outside_range = {
            name: max(max(0.0, native_gap_ranges[name]["min"] - min(row[name] for row in combined_gaps)),
                      max(0.0, max(row[name] for row in combined_gaps) - native_gap_ranges[name]["max"]))
            for name in native_gap_ranges}
        baseline_gap_change_right = {
            key: max(abs(baseline_gaps_right[i][key] - combined_gaps_right[i][key])
                     for i in range(len(frames))
                     ) for key in baseline_gaps_right[0]}
        native_gap_outside_range_right = {
            name: max(max(0.0, native_gap_ranges[name]["min"] - min(row[name] for row in combined_gaps_right)),
                      max(0.0, max(row[name] for row in combined_gaps_right) - native_gap_ranges[name]["max"]))
            for name in native_gap_ranges}
        semantic_error = np.asarray(semantic_target_rows) - source_semantics
        semantic_error_right = np.asarray(semantic_target_right_rows) - source_semantics_right
        if os.path.isfile(args.v1_report):
            with open(args.v1_report, "r", encoding="utf-8") as stream:
                v1_report = json.load(stream)
            v1_rmse = v1_report.get("source_target_semantic_rmse",
                v1_report.get("preview", {}).get("semantic_feature_rmse_after_solve", {}))
            v1_right_rmse = v1_report.get("right_source_target_semantic_rmse", v1_rmse)
        else:
            raise RuntimeError("v1 residual report is required for the improvement gate")
        prior_left_rmse = prior_right_rmse = None
        if args.comparison_report:
            with open(args.comparison_report, "r", encoding="utf-8") as stream:
                previous_fit_report = json.load(stream)
            prior_left_rmse = previous_fit_report.get("source_target_semantic_rmse")
            prior_right_rmse = previous_fit_report.get("right_source_target_semantic_rmse")
        else:
            previous_fit_report = None
        full_pose_bones = sorted(set(bones) | {
            LEFT_TO_RIGHT_BONES[name] for name in bones if name in LEFT_TO_RIGHT_BONES})
        full_pose_continuity_after = pose_sequence_jump_report(
            combined_pose_rows, full_pose_bones, target_rest_all, frames,
            armature_length_scale)
        full_pose_continuity_before = None
        if previous_fit_report is not None:
            previous_rows = previous_fit_report.get("preview", {}).get(
                "bake_pose_reference", {}).get("matrices")
            if previous_rows and len(previous_rows) == len(frames):
                full_pose_continuity_before = pose_sequence_jump_report(
                    previous_rows, full_pose_bones, target_rest_all, frames,
                    armature_length_scale)
        left_glove_gap_max = max(float(row["wrist_glove"]) for row in combined_gaps)
        right_glove_gap_max = max(float(row["wrist_glove"]) for row in combined_gaps_right)
        glove_gap_max = max(left_glove_gap_max, right_glove_gap_max)
        glove_gap_limit = float(native_gap_ranges["wrist_glove"]["max"]) + 0.02
        current_rmse = {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                        for i, name in enumerate(SEMANTIC_NAMES)}
        current_rmse_right = {name: float(np.sqrt(np.mean(semantic_error_right[:, i] ** 2)))
                              for i, name in enumerate(SEMANTIC_NAMES)}
        target_semantic_array = np.asarray(semantic_target_rows, dtype=np.float64)
        target_semantic_right_array = np.asarray(semantic_target_right_rows, dtype=np.float64)
        source_ranges = np.ptp(source_semantics, axis=0)
        source_ranges_right = np.ptp(source_semantics_right, axis=0)
        target_ranges = np.ptp(target_semantic_array, axis=0)
        target_ranges_right = np.ptp(target_semantic_right_array, axis=0)
        semantic_motion_retention = {
            "left": {name: (float(target_ranges[i] / source_ranges[i])
                            if source_ranges[i] > 0.05 else None)
                     for i, name in enumerate(SEMANTIC_NAMES)},
            "right": {name: (float(target_ranges_right[i] / source_ranges_right[i])
                             if source_ranges_right[i] > 0.05 else None)
                      for i, name in enumerate(SEMANTIC_NAMES)}}
        motion_retention_floor = 0.35
        retained_motion_values = [value for side in semantic_motion_retention.values()
                                  for value in side.values() if value is not None]
        semantic_motion_gate = bool(retained_motion_values) and min(retained_motion_values) >= motion_retention_floor
        semantic_groups = {
            "shoulder_orientation": (0, 1, 2), "elbow_flexion": (3,),
            "forearm_roll": (4,), "wrist_orientation": (5, 6, 7),
            "arm_plane_hand_position": (8, 9, 10)}
        group_comparison = {}
        for label, indices in semantic_groups.items():
            names = [SEMANTIC_NAMES[i] for i in indices]
            before = math.sqrt(sum(float(v1_rmse[name]) ** 2 for name in names) / len(names))
            before_right = math.sqrt(sum(float(v1_right_rmse[name]) ** 2 for name in names) / len(names))
            after = math.sqrt(sum(current_rmse[name] ** 2 for name in names) / len(names))
            after_right = math.sqrt(sum(current_rmse_right[name] ** 2 for name in names) / len(names))
            group_comparison[label] = {
                "previous_left_rmse": before, "current_left_rmse": after,
                "left_relative_change": (before - after) / max(before, 1e-12),
                "previous_right_rmse": before_right, "current_right_rmse": after_right,
                "right_relative_change": (before_right - after_right) / max(before_right, 1e-12)}
        worst_frame_index = int(np.argmax(np.max(np.abs(semantic_error), axis=1)))
        worst_semantic = {"frame": frames[worst_frame_index],
            "feature": SEMANTIC_NAMES[int(np.argmax(np.abs(semantic_error[worst_frame_index])))],
            "absolute_residual": float(np.max(np.abs(semantic_error[worst_frame_index]))),
            "per_feature_absolute_residual": {name: float(abs(semantic_error[worst_frame_index, i]))
                for i, name in enumerate(SEMANTIC_NAMES)}}
        worst_right_index = int(np.argmax(np.max(np.abs(semantic_error_right), axis=1)))
        worst_semantic_right = {"frame": frames[worst_right_index],
            "feature": SEMANTIC_NAMES[int(np.argmax(np.abs(semantic_error_right[worst_right_index])))],
            "absolute_residual": float(np.max(np.abs(semantic_error_right[worst_right_index]))),
            "per_feature_absolute_residual": {
                name: float(abs(semantic_error_right[worst_right_index, i]))
                for i, name in enumerate(SEMANTIC_NAMES)}}
        diagnostic_frames = sorted(set(range(38, 45)) | set(range(47, 54)))
        def sequence_window_records(side):
            semantic_offset = 0 if side == "left" else sem_count
            gap_offset = 0 if side == "left" else 4
            source_rows = source_semantics if side == "left" else source_semantics_right
            final_sem = np.asarray(semantic_target_rows if side == "left"
                                   else semantic_target_right_rows)
            gap_rows = combined_gaps if side == "left" else combined_gaps_right
            scale_values = left_gap_scales if side == "left" else right_gap_scales
            rows = []
            for frame in diagnostic_frames:
                i = frames.index(frame)
                node_id = selected_node_ids[i]
                selected_candidate = selected_layer_indices[i]
                state_sem = candidate_semantics_by_frame[i][selected_candidate,
                    semantic_offset:semantic_offset + sem_count]
                state_gaps = candidate_gaps_by_frame[i][selected_candidate,
                    gap_offset:gap_offset + 4]
                transition_kind = ("start" if i == 0 else
                    graph_path["transitions"][i - 1]["kind"])
                rows.append({
                    "frame": frame,
                    "native_action": native_action_names[node_id],
                    "native_sample_index": int(native_sample_indices[node_id]),
                    "native_time": float(native_times[node_id]),
                    "path_transition_kind": transition_kind,
                    "nearest_native_action": train_sample_action_names[int(nearest_indices[i])],
                    "nearest_semantic_distance": float(train_distance[int(nearest_indices[i]), i]),
                    "source_elbow_radians": float(source_rows[i, 3]),
                    "nearest_native_elbow_radians": float(
                        state_sem[3] * sem_std[3] + sem_mean[3]),
                    "selected_path_elbow_radians": float(final_sem[i, 3]),
                    "joint_gaps_armature_units": {name: float(gap_rows[i][name])
                                                  for name in gap_rows[i]},
                    "nearest_native_gaps_armature_units": {
                        name: float(state_gaps[gap_index])
                        for gap_index, name in enumerate(GAP_PAIRS)},
                })
            jumps = np.diff(final_sem[:, 3])
            max_j = int(np.argmax(np.abs(jumps)))
            return {"windows": rows,
                "max_elbow_jump_radians": float(abs(jumps[max_j])),
                "max_elbow_jump_interval": [int(frames[max_j]), int(frames[max_j + 1])],
                "max_absolute_source_elbow_step_radians": float(np.max(np.abs(np.diff(source_rows[:, 3])))),
                "joint_gap_scales": list(scale_values)}

        left_sequence_diagnostics = sequence_window_records("left")
        right_sequence_diagnostics = sequence_window_records("right")
        selected_semantic_error = path_semantic_rows - source_graph_semantics
        graph_transitions = graph_path["transitions"]
        objective_terms = {
            "semantic_path_mse": float(np.mean(selected_semantic_error ** 2)),
            "full_pose_transition_mean_rms": float(np.mean(
                [row["rms_jump"] for row in graph_transitions])) if graph_transitions else 0.0,
            "semantic_velocity_error_mean": float(np.mean(
                [row["velocity_error"] for row in graph_transitions])) if graph_transitions else 0.0,
            "phase_timing_error_mean": float(np.mean(
                [row["phase_error"] for row in graph_transitions])) if graph_transitions else 0.0,
            "graph_path_total_cost": graph_path["total_cost"],
            "graph_state_cost": graph_path["state_cost"]}
        graph_path_rows = [{
            "frame": int(frames[i]), "node_id": int(node_id),
            "action": native_action_names[node_id],
            "sample_index": int(native_sample_indices[node_id]),
            "native_time": float(native_times[node_id]),
            "normalized_native_time": float(native_normalized_times[node_id]),
            "transition_kind": "start" if i == 0 else graph_transitions[i - 1]["kind"],
            "semantic_state_cost": float(candidate_costs_by_frame[i][selected_layer_indices[i]]),
        } for i, node_id in enumerate(selected_node_ids)]
        graph_state_switches = sum(
            graph_path_rows[i]["action"] != graph_path_rows[i - 1]["action"]
            for i in range(1, len(graph_path_rows)))
        feature_min, feature_max = source_semantics.min(axis=0), source_semantics.max(axis=0)
        nearest_d = train_distance.min(axis=0)
        weighted_mesh_centroids = {}
        for obj in target_meshes:
            vertex_group_index = {group.index: group.name for group in obj.vertex_groups}
            for group in obj.vertex_groups:
                if group.name not in arm_bones:
                    continue
                indices, weights = [], []
                for vertex in obj.data.vertices:
                    for assignment in vertex.groups:
                        if assignment.group == group.index and assignment.weight > 0:
                            indices.append(vertex.index)
                            weights.append(float(assignment.weight))
                weighted_mesh_centroids.setdefault(group.name, {})[obj.name] = {
                    "weighted_vertex_count": len(indices),
                    "weight_sum": float(sum(weights))}

        report = {
            "schema": "mg1.semantic-luigi-walk-to-waluigi-both-arms.v1",
            "source_rig": source_rig.name, "source_action": source_action.name,
            "source_slot": source_slot.identifier, "source_frames": frames,
            "target_rig": target_original.name, "target_walk_action": walk_source_action.name,
                "target_walk_source_slot": walk_source_slot.identifier,
                "target_walk_duplicate_slot": walk_slot.identifier,
                "target_walk_curves_imported_from_live_bridge": walk_curve_payload is not None,
                "target_bones": bones,
            "semantic_feature_names": list(SEMANTIC_NAMES),
            "elbow_source_mapping": {
                "definition": "source flexion excursion relative to frame 0; target rest-relative neutral is zero",
                "raw_source_range_radians": [float(raw_source_semantics[:, 3].min()),
                                             float(raw_source_semantics[:, 3].max())],
                "frame_0_reference_radians": source_elbow_reference,
                "excursion_gain": float(args.elbow_gain),
                "mapped_source_range_radians": [float(source_semantics[:, 3].min()),
                                                float(source_semantics[:, 3].max())],
                "semantic_feature_weighting": "equal squared standardized residuals",
                "target_native_training_range_radians": [float(sem_train[:, 3].min()),
                                                          float(sem_train[:, 3].max())]},
            "right_arm_mapping": {
                "source_bones": {key: SOURCE_PROFILE_RIGHT[key] for key in
                    ("shoulder", "upper", "elbow_a", "elbow_b", "forearm", "forearm_overlap", "wrist", "hand")},
                "target_bones": {key: TARGET_PROFILE_RIGHT[key] for key in
                    ("shoulder", "upper", "elbow_a", "elbow_b", "forearm", "forearm_overlap", "wrist", "hand")},
                "left_to_right_deform_bone_map": LEFT_TO_RIGHT_BONES,
                "method": "paired left/right semantic features select one raw native Waluigi state; final composition mirrors that state into the homologous right basis",
                "branch_constraints_independent": True,
                "finger_bones_preserved_from_baseline": [f"bone_{i}" for i in range(66, 87)]},
            "right_elbow_source_mapping": {
                "definition": "right source flexion excursion relative to frame 0 in the canonical semantic frame",
                "raw_source_range_radians": [float(raw_source_semantics_right[:, 3].min()),
                                             float(raw_source_semantics_right[:, 3].max())],
                "frame_0_reference_radians": source_elbow_reference_right,
                "excursion_gain": float(args.elbow_gain),
                "mapped_source_range_radians": [float(source_semantics_right[:, 3].min()),
                                                float(source_semantics_right[:, 3].max())],
                "semantic_feature_weighting": "equal squared standardized residuals"},
            "source_target_semantic_rmse": {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                                              for i, name in enumerate(SEMANTIC_NAMES)},
            "right_source_target_semantic_rmse": current_rmse_right,
            "semantic_rmse_delta_vs_previous_fit": ({
                "left": {name: current_rmse[name] - float(prior_left_rmse[name])
                         for name in SEMANTIC_NAMES},
                "right": {name: current_rmse_right[name] - float(prior_right_rmse[name])
                          for name in SEMANTIC_NAMES},
                "previous_report": os.path.abspath(args.comparison_report)}
                if prior_left_rmse and prior_right_rmse else None),
            "semantic_motion_retention": {
                "range_ratio_target_over_source": semantic_motion_retention,
                "minimum_required_ratio": motion_retention_floor,
                "gate": "pass" if semantic_motion_gate else "fail",
                "source_range_left": {name: float(source_ranges[i])
                                      for i, name in enumerate(SEMANTIC_NAMES)},
                "target_range_left": {name: float(target_ranges[i])
                                      for i, name in enumerate(SEMANTIC_NAMES)},
                "source_range_right": {name: float(source_ranges_right[i])
                                       for i, name in enumerate(SEMANTIC_NAMES)},
                "target_range_right": {name: float(target_ranges_right[i])
                                       for i, name in enumerate(SEMANTIC_NAMES)}},
            "right_worst_semantic_residual": worst_semantic_right,
            "right_elbow_curve_radians": [{"frame": int(frame),
                "source": float(source_semantics_right[i, 3]),
                "target": float(semantic_target_right_rows[i][3])}
                for i, frame in enumerate(frames)],
            "residual_comparison_to_v1": {"v1_rmse": v1_rmse,
                "current_rmse": current_rmse, "semantic_group_rmse": group_comparison,
                "worst_frame": worst_semantic},
            "source_semantic_range": {name: [float(feature_min[i]), float(feature_max[i])]
                                      for i, name in enumerate(SEMANTIC_NAMES)},
            "native_training_semantic_range": {name: [float(sem_train[:, i].min()),
                                                       float(sem_train[:, i].max())]
                                                for i, name in enumerate(SEMANTIC_NAMES)},
            "nearest_native_semantic_distance": {"mean": float(nearest_d.mean()),
                                                  "max": float(nearest_d.max())},
            "sequence_joint_fit": {
                "method": "layered dynamic programming over exact raw-ANM native pose samples; no free pose coordinates and no post-path smoothing",
                "graph": {"node_count": native_graph["node_count"],
                    "temporal_edge_count": native_graph["temporal_edge_count"],
                    "semantic_neighbor_edge_count": native_graph["neighbor_edge_count"],
                    "candidate_states_per_frame": [int(len(row)) for row in candidate_ids_by_frame],
                    "semantic_neighbor_count": args.graph_semantic_neighbors,
                    "semantic_neighbor_radius_standardized": args.graph_semantic_radius,
                    "candidate_limit_per_frame": args.graph_candidates,
                    "strict_max_transition_jump": 0.5,
                    "strict_glove_gap_limit_armature_units": 0.02},
                "path_behavior": {"total_cost": graph_path["total_cost"],
                    "temporal_transitions": graph_path["temporal_transition_count"],
                    "neighbor_transitions": graph_path["neighbor_transition_count"],
                    "hold_transitions": graph_path["hold_transition_count"],
                    "action_switches": int(graph_state_switches),
                    "maximum_transition_jump": graph_path["maximum_transition_jump"],
                    "path": graph_path_rows},
                "left": left_sequence_diagnostics,
                "right": right_sequence_diagnostics,
                "left_objective_history": left_joint_history,
                "right_objective_history": right_joint_history,
                "edge_costs": {"semantic_state_distance": "sum of squared standardized 22-dimensional source-target residual",
                    "full_pose_transition_weight": 8.0,
                    "semantic_velocity_weight": 1.0,
                    "semantic_velocity_distance": "sum of squared standardized 22-dimensional source-vs-native step residual",
                    "neighbor_switch_cost": 4.0,
                    "native_action_switch_cost": 24.0,
                    "hold_cost": 12.0,
                    "phase_timing_weight": 0.5},
                "target_gap_ranges_armature_units": native_gap_ranges,
                "full_pose_continuity": {
                    "representation": "per-bone rest-relative SE(3): translation normalized by upper-arm length plus quaternion-sign-invariant rotation angle",
                    "modeled_bones_including_both_sides": full_pose_bones,
                    "native_p99_single-bone_step_per_source_frame": native_pose_step_limit,
                    "before": full_pose_continuity_before,
                    "after": full_pose_continuity_after,
                    "requested_frame_pairs": ["31->33", "40->43", "48->51"]},
                "weighted_mesh_centroid_continuity": {
                    "method": "per-vertex-group weighted rest centroid transformed by selected final composed native pose and expressed in torso space",
                    "regions": ["arm_union", "shoulder", "upper_arm", "elbow", "forearm", "glove_hand"],
                    "mesh_group_bones_available": sorted(mesh_centers),
                    "normalized_by_armature_length": armature_length_scale},
                "wrist_glove_attachment": {
                    "left_max_gap_armature_units": left_glove_gap_max,
                    "right_max_gap_armature_units": right_glove_gap_max,
                    "max_gap_armature_units": glove_gap_max,
                    "native_range": native_gap_ranges["wrist_glove"],
                    "allowed_max_gap_armature_units": glove_gap_limit}},
            "target_model": {"type": "raw-ANM native-pose graph",
                "pose_descriptor_dimensions": int(len(bones) * 7),
                "source_schema": dataset["source_schema"],
                "source_authority": dataset["source_authority"],
                "training_actions": [row["action"] for row in target_actions],
                "training_samples": int(training_sample_count), "holdout_actions_validation_only": [row["action"] for row in target_holdouts],
                "holdout_samples": int(sum(len(row["samples"]) for row in target_holdouts)),
                "holdout_actions_used_by_graph": False},
            "optimization": {"objective_terms": objective_terms,
                "method": "discrete shortest path through semantically ranked raw native samples; transition constraints enforce composed-pose jump and glove-gap limits",
                "semantic_path_mse": objective_terms["semantic_path_mse"],
                "graph_path_total_cost": graph_path["total_cost"],
                "no_free_pose_dofs": True,
                "post_path_smoothing": False},
            "preview": {"file": os.path.abspath(args.preview), "collection": preview_collection.name,
                "root": None, "rig": target_copy.name, "action": baked_action.name,
                "mesh_objects": [obj.name for obj in target_meshes],
                "baseline_noop_geometry_check": baseline_geometry_check,
                "object_scale_unchanged": [float(v) for v in target_copy.scale],
                "rig_world_matrix": matrix_rows(target_copy.matrix_world),
                "constraints_removed_from_duplicate": cleared_constraints,
                "runtime_constraints_remaining": sum(len(obj.constraints) +
                    sum(len(pb.constraints) for pb in obj.pose.bones) for obj in (target_copy,)),
                "max_pose_write_matrix_error": max_pose_write_error,
                "max_pose_write_divergence": max_pose_write_detail,
                "bake_pose_reference": {
                    "space": "armature-space evaluated pose.matrix before action baking",
                    "frames": [int(frame) for frame in frames],
                    "matrices": [{name: matrix_rows(matrix)
                                  for name, matrix in sorted(row.items())}
                                 for row in combined_pose_rows],
                    "requested_matrices": [{name: matrix_rows(matrix)
                                             for name, matrix in sorted(row.items())}
                                            for row in intended_pose_rows],
                    "basis_matrices": [{name: matrix_rows(matrix)
                                        for name, matrix in sorted(bases.items())}
                                       for _frame, bases in basis_samples],
                },
                "worst_pose_write_bones": sorted(pose_write_error_rows,
                    key=lambda row: row["error"], reverse=True)[:12],
                "max_pose_scale_delta_vs_validated_walk": max_pose_scale_delta_vs_walk,
                "max_nonmodeled_bone_pose_matrix_error_vs_walk": max_nonmodel_pose_error,
                "max_composition_baseline_pose_matrix_error": max_composition_baseline_pose_error,
                "max_foot_tail_error_vs_walk": max_foot_baseline_error,
                "max_head_cap_centroid_error_vs_walk": max_cap_baseline_error,
                "max_baked_action_replay_matrix_error": max_baked_replay_error,
                "max_baked_action_vs_evaluated_fit_pose_matrix_error": max_baked_replay_error,
                "max_requested_vs_evaluated_fit_pose_matrix_error": max_pose_write_error,
                "left_max_joint_gap_change_vs_validated_walk": baseline_gap_change,
                "right_max_joint_gap_change_vs_validated_walk": baseline_gap_change_right,
                "max_right_root_anchor_translation_correction": max(right_root_anchor_corrections),
                "max_right_finger_local_matrix_error_vs_clean_walk": max_right_finger_local_error,
                "walk_root_world_matrix": matrix_rows(target_copy.matrix_world),
                "native_training_joint_gap_ranges_armature_units": native_gap_ranges,
                "left_max_joint_gap_outside_native_range_armature_units": native_gap_outside_range,
                "right_max_joint_gap_outside_native_range_armature_units": native_gap_outside_range_right,
                "semantic_feature_rmse_after_solve": {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                                                        for i, name in enumerate(SEMANTIC_NAMES)},
                "semantic_feature_rmse_by_side": {"left": current_rmse,
                                                   "right": current_rmse_right},
                "finite_value_failures": mesh_finite_failures,
                "sampled_frames_for_visual_review": [0, 15, 30, 45, 60]},
            "safety": {"production_objects_mutated": False, "production_actions_mutated": False,
                       "source_action_assigned_to_duplicate": False,
                       "target_experimental_action_is_new": True}}

        report["validation"] = {
            "fit_gate": "pass" if not mesh_finite_failures and max_pose_write_error < 2e-3 and
                         max_baked_replay_error < 1e-4 and
                         left_sequence_diagnostics["max_elbow_jump_radians"] <= 0.15 and
                         right_sequence_diagnostics["max_elbow_jump_radians"] <= 0.15 and
                         max_nonmodel_pose_error < 2e-3 and
                         max_composition_baseline_pose_error < 1e-5 and
                         max_right_finger_local_error < 2e-3 and
                         max_foot_baseline_error < 2e-4 and
                         max_cap_baseline_error < 2e-4 and
                         full_pose_continuity_after["max_adjacent"]["normalized_se3"] <
                             0.5 and
                         glove_gap_max < 0.02 and semantic_motion_gate and
                         all(value < 0.02 for value in native_gap_outside_range.values()) and
                         all(value < 0.02 for value in native_gap_outside_range_right.values()) else "fail",
            "semantic_fit_gate": "not_applied_new_native_model",
            "previous_model_comparison": "informational; native target action coverage and split changed",
            "max_pose_write_matrix_diagnostic_tolerance": 2e-3,
            "max_baked_action_replay_matrix_tolerance": 1e-4,
            "max_baked_action_vs_evaluated_fit_pose_matrix_tolerance": 1e-4,
            "max_requested_vs_evaluated_fit_pose_matrix_tolerance": 2e-3,
            "max_nonmodeled_pose_error_tolerance": 2e-3,
            "max_foot_cap_baseline_error_tolerance": 2e-4,
            "max_joint_gap_outside_native_range_tolerance_armature_units": 0.02,
            "max_final_pose_jump_strict_tolerance_normalized_se3": 0.5,
            "max_glove_gap_strict_tolerance_armature_units": 0.02,
            "semantic_motion_retention": "pass" if semantic_motion_gate else "fail",
            "finite_pose_mesh_values": "pass" if not mesh_finite_failures else "fail"}

        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        if report["validation"]["fit_gate"] != "pass":
            report["preview"]["requested_output_path"] = report["preview"]["file"]
            report["preview"]["candidate_path"] = None
            report["preview"]["file"] = None
            report["preview"]["visual_review_candidate"] = False
            report["preview"]["candidate_emitted"] = False
            with open(args.report, "w", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, allow_nan=False)
                stream.write("\n")
            print("MG1_SEMANTIC_WALK_FIT=" + json.dumps({
                "preview": None, "candidate_path": None,
                "action": baked_action.name, "validation": report["validation"],
                "semantic_rmse": report["preview"]["semantic_feature_rmse_after_solve"]}))
            raise RuntimeError("semantic walk fit failed numerical gate; no visual preview emitted")
        report["preview"]["visual_review_candidate"] = True
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
        for obj in old_objects:
            obj.hide_set(True)
            obj.hide_viewport = True
            obj.hide_render = True
        for obj in created_objects:
            # Object copies inherit source-level viewport hiding independently
            # from hide_set(); clear both before saving the standalone preview.
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_set(False)
        enable_preview_collection(scene, bpy.context.view_layer, preview_collection)
        for obj in target_meshes:
            armature_modifiers = [modifier for modifier in obj.modifiers
                                  if modifier.type == "ARMATURE"]
            if not armature_modifiers or any(modifier.object != target_copy
                                             for modifier in armature_modifiers):
                raise RuntimeError(f"preview mesh {obj.name} is not bound to the preview rig")
            if obj.parent != target_copy:
                raise RuntimeError(f"preview mesh {obj.name} is not parented to the preview rig")
            if max(abs(float(value) - 1.0) for value in obj.scale) > 1e-4:
                raise RuntimeError(f"preview mesh {obj.name} has an unexpected local object scale")
            if any(not modifier.show_viewport or not modifier.show_render or
                   not modifier.use_vertex_groups
                   for modifier in armature_modifiers):
                raise RuntimeError(f"preview mesh {obj.name} has disabled armature deformation")
            if preview_collection not in obj.users_collection:
                raise RuntimeError(f"preview mesh {obj.name} is not linked to its preview collection")
        bpy.context.view_layer.objects.active = target_copy
        target_copy.select_set(True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
        print("MG1_SEMANTIC_WALK_FIT=" + json.dumps({
            "preview": report["preview"]["file"], "action": baked_action.name,
            "validation": report["validation"],
            "semantic_rmse": report["preview"]["semantic_feature_rmse_after_solve"],
            "graph": report["sequence_joint_fit"]["graph"]}))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        # Preserve the saved preview file, but clean temporary data from this
        # background session. No input .blend is saved.
        for obj in list(bpy.data.objects):
            if obj in old_objects:
                try:
                    obj.hide_set(old_obj_visibility[obj])
                    obj.hide_viewport = old_obj_hide_viewport[obj]
                    obj.hide_render = old_obj_hide_render[obj]
                except Exception:
                    pass
                continue
            data = obj.data
            object_type = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if object_type == "MESH":
                    bpy.data.meshes.remove(data)
                elif object_type == "ARMATURE":
                    bpy.data.armatures.remove(data)
        for action in list(bpy.data.actions):
            if action not in old_actions:
                bpy.data.actions.remove(action, do_unlink=True)
        for collection in list(bpy.data.collections):
            if collection not in old_collections:
                bpy.data.collections.remove(collection)
        bpy.context.view_layer.update()


if __name__ == "__main__":
    main()
