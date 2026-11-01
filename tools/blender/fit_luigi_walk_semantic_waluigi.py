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
from fit_native_context_pca import fit as fit_pca
from semantic_arm_fit_math import (
    encode_context_sample, farthest_centers, fit_rbf_ridge,
    mirror_pose_across_sagittal, predict_rbf, semantic_features,
    smooth_latent_sequence,
)

HOLDOUTS = {"sequence_11.003", "sequence_15.003", "sequence_20.001",
            "sequence_24", "sequence_26"}
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
                  "wrist_rot_y", "wrist_rot_z", "hand_delta_x",
                  "hand_delta_y", "hand_delta_z")


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
    parser.add_argument("--latent-dimensions", type=int, default=192)
    parser.add_argument("--inverse-native-weight", type=float, default=0.01)
    parser.add_argument("--inverse-iterations", type=int, default=48)
    parser.add_argument("--elbow-weight", type=float, default=16.0,
                        help="extra multiplier on standardized elbow-flexion residual")
    parser.add_argument("--elbow-gain", type=float, default=1.5,
                        help="scale source frame-relative flexion excursion for target profile")
    parser.add_argument("--sequence-native-weight", type=float, default=0.01)
    parser.add_argument("--sequence-velocity-weight", type=float, default=0.002)
    parser.add_argument("--sequence-acceleration-weight", type=float, default=0.01)
    parser.add_argument("--rbf-centers", type=int, default=72)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--v1-report", default="tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_fit.json")
    return parser.parse_args(argv)


def matrix_rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def numpy_matrix(m):
    return np.asarray([[float(m[r][c]) for c in range(4)] for r in range(4)],
                      dtype=np.float64)


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
    source_frame = proper_rotation(source_obj.matrix_world) @ numpy_matrix(source_rest[source_bone]["rest_matrix"])[:3, :3]
    target_frame = proper_rotation(target_obj.matrix_world) @ numpy_matrix(target_rest[target_bone]["rest_matrix"])[:3, :3]
    return target_frame.T @ source_frame


def target_rest_from_dataset(dataset):
    return dataset["rest_bones"]


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
                                profile=SOURCE_PROFILE):
    return semantic_features(rest, pose, profile, body_map, forearm_map)


def semantic_from_target_sample(rest, pose, profile=TARGET_PROFILE,
                                forearm_frame_map=None):
    return semantic_features(rest, pose, profile, forearm_frame_map=forearm_frame_map)


def folds_by_action(action_rows, count):
    bins, totals = [[] for _ in range(count)], [0] * count
    for row in sorted(action_rows, key=lambda x: (-len(x["samples"]), x["action"])):
        index = min(range(count), key=lambda i: (totals[i], i))
        bins[index].append(row["action"])
        totals[index] += len(row["samples"])
    return [set(group) for group in bins if group]


def decode_features(z_normalized, latent_std, pca_model):
    mean, scale, components = pca_model
    z = np.asarray(z_normalized, dtype=np.float64) * latent_std
    return (z @ components) * scale + mean


def exp_rotation(rotvec):
    v = np.asarray(rotvec, dtype=np.float64)
    angle = float(np.linalg.norm(v))
    if angle < 1e-12:
        return np.eye(3)
    axis = v / angle
    x, y, z = axis
    k = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(angle) * k + (1.0 - math.cos(angle)) * (k @ k)


def decode_context_poses(x, bones, rest_bones):
    poses = {}
    for i, name in enumerate(bones):
        row = x[i * 6:i * 6 + 6]
        delta = np.eye(4)
        delta[:3, :3] = exp_rotation(row[3:6])
        delta[:3, 3] = row[:3]
        poses[name] = numpy_matrix(rest_bones[name]["rest_matrix"]) @ delta
    return poses


GAP_PAIRS = {"shoulder": ("bone_21", "tail", "bone_22", "head"),
             "elbow_main": ("bone_24", "tail", "bone_26", "head"),
             "elbow_overlap": ("bone_25", "tail", "bone_29", "head"),
             "wrist_glove": ("bone_27", "tail", "bone_30", "head")}


def context_joint_gaps(pose, rest_bones):
    points = {}
    for _label, (a, ea, b, eb) in GAP_PAIRS.items():
        for name, endpoint in ((a, ea), (b, eb)):
            key = (name, endpoint)
            if key in points:
                continue
            rest = numpy_matrix(rest_bones[name]["rest_matrix"])
            local_point = np.r_[rest_bones[name][endpoint], 1.0]
            points[key] = (_matrix_for_fit(pose[name]) @ np.linalg.inv(rest) @ local_point)[:3]
    return np.asarray([np.linalg.norm(points[(a, ea)] - points[(b, eb)])
                       for a, ea, b, eb in GAP_PAIRS.values()], dtype=np.float64)


def _matrix_for_fit(value):
    return np.asarray(value, dtype=np.float64).reshape(4, 4)


def target_semantics_from_latent(z_normalized, latent_std, pca_model, bones,
                                 rest_bones, sem_mean, sem_std,
                                 native_gap_targets, gap_scales):
    features = decode_features(z_normalized, latent_std, pca_model)
    pose = decode_context_poses(features, bones, rest_bones)
    semantic = semantic_from_target_sample(rest_bones, pose)
    gap_delta = (context_joint_gaps(pose, rest_bones) - native_gap_targets) / gap_scales
    return np.r_[(semantic - sem_mean) / sem_std, gap_delta]


def solve_semantic_latents(source_semantics, native_prior, latent_std, pca_model,
                           bones, rest_bones, sem_mean, sem_std,
                           native_gap_targets, native_weight=0.01, iterations=24,
                           initial_proposal=None, elbow_weight=1.0,
                           gap_scales=(0.01, 0.005, 0.005, 0.005)):
    """Invert the PCA pose manifold for semantic targets, regularized to native poses."""
    targets = np.c_[(np.asarray(source_semantics, dtype=np.float64) - sem_mean) / sem_std,
                     np.zeros((len(source_semantics), len(GAP_PAIRS)), dtype=np.float64)]
    priors = np.asarray(native_prior, dtype=np.float64)
    solutions, costs = [], []
    dim = priors.shape[1]
    starts = (None if initial_proposal is None else
              np.asarray(initial_proposal, dtype=np.float64))
    for index, (target, prior) in enumerate(zip(targets, priors)):

        def residual(value):
            result = target_semantics_from_latent(value, latent_std, pca_model,
                bones, rest_bones, sem_mean, sem_std, native_gap_targets,
                np.asarray(gap_scales, dtype=np.float64)) - target
            result[3] *= elbow_weight
            return result

        candidates = [prior.copy()]
        if starts is not None:
            candidates.append(starts[index].copy())
        z = min(candidates, key=lambda value: float(
            residual(value) @ residual(value) +
            native_weight * np.sum((value - prior) ** 2)))
        for _ in range(iterations):
            r = residual(z)
            jac = np.empty((len(r), dim), dtype=np.float64)
            eps = 1e-4
            for column in range(dim):
                shifted = z.copy()
                shifted[column] += eps
                jac[:, column] = (residual(shifted) - r) / eps
            gradient = jac.T @ r + native_weight * (z - prior)
            # Woodbury form keeps the solve in 11 semantic dimensions instead
            # of factoring a 192 x 192 latent matrix for every frame/iteration.
            semantic_hessian = np.eye(len(r), dtype=np.float64) + (jac @ jac.T) / native_weight
            step = (gradient - jac.T @ np.linalg.solve(
                semantic_hessian, jac @ gradient / native_weight)) / native_weight
            norm = float(np.linalg.norm(step))
            if norm < 1e-7:
                break
            if norm > 2.0:
                step *= 2.0 / norm
            old_cost = float(r @ r + native_weight * np.sum((z - prior) ** 2))
            accepted = False
            # Strong semantic channels (notably elbow flexion) can have a
            # narrow useful step around the curved PCA manifold. Backtrack
            # further before declaring the local solve stuck.
            for fraction in (1.0, 0.5, 0.25, 0.125):
                candidate = z - fraction * step
                rc = residual(candidate)
                cost = float(rc @ rc + native_weight * np.sum((candidate - prior) ** 2))
                if cost < old_cost:
                    z, accepted = candidate, True
                    break
            if not accepted:
                break
        final_residual = residual(z)
        solutions.append(z)
        costs.append(float(final_residual @ final_residual +
                           native_weight * np.sum((z - prior) ** 2)))
    return np.asarray(solutions), costs


def assign_pose_matrices_hierarchical(rig, pose_matrices):
    """Assign armature-space matrices parent-first through Blender's pose API."""
    depths = {}
    remaining = set(rig.data.bones.keys())
    while remaining:
        progressed = False
        for name in list(remaining):
            parent = rig.data.bones[name].parent
            if parent and parent.name in remaining:
                continue
            depths[name] = depths[parent.name] + 1 if parent else 0
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise RuntimeError("target armature hierarchy cannot be assigned parent-first")
    for depth in sorted(set(depths.values())):
        for name in rig.data.bones.keys():
            if depths[name] == depth:
                rig.pose.bones[name].rotation_mode = "QUATERNION"
                rig.pose.bones[name].matrix = Matrix(pose_matrices[name])
        bpy.context.view_layer.update()
    return {name: rig.pose.bones[name].matrix_basis.copy()
            for name in rig.data.bones.keys()}


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
        dataset = json.load(stream)
    bones = list(dataset["bones"])
    if len(bones) != 38 or set(HOLDOUTS) != set(dataset["holdout_actions"]):
        raise RuntimeError("target profile/split is not the validated 38-bone, 23/5 dataset")
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
    if abs(float(baseline_original.matrix_world.to_scale()[0]) - 43.437) > 0.01:
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
    x_by_action, s_by_action = {}, {}
    for action_row in dataset["actions"]:
        x_by_action[action_row["action"]] = np.asarray(
            [encode_context_sample(sample, bones) for sample in action_row["samples"]], dtype=np.float64)
        rest = target_rest
        s_by_action[action_row["action"]] = np.asarray([
            semantic_from_target_sample(rest, pose_dict_from_sample(sample, bones))
            for sample in action_row["samples"]], dtype=np.float64)
    x_train = np.concatenate([x_by_action[row["action"]] for row in target_actions])
    sem_train = np.concatenate([s_by_action[row["action"]] for row in target_actions])
    pca = fit_pca(x_train, args.latent_dimensions)
    pca_mean, pca_scale, pca_components = pca
    latent_train = ((x_train - pca_mean) / pca_scale) @ pca_components.T
    latent_std = np.maximum(latent_train.std(axis=0), 1e-6)
    latent_train_norm = latent_train / latent_std

    sem_mean = sem_train.mean(axis=0)
    sem_std = np.maximum(sem_train.std(axis=0), 1e-5)
    sem_train_norm = (sem_train - sem_mean) / sem_std
    distances = np.linalg.norm(sem_train_norm[:, None, :] - sem_train_norm[None, :, :], axis=2)
    nonzero_distances = distances[distances > 1e-10]
    sigma = float(np.median(nonzero_distances))
    folds = folds_by_action(target_actions, args.cv_folds)
    width_factors = (0.5, 1.0, 2.0)
    alphas = (1e-3, 1e-2, 1e-1)
    cv_rows = []
    for width_factor in width_factors:
        gamma = 1.0 / (2.0 * (sigma * width_factor) ** 2)
        for alpha in alphas:
            fold_errors = []
            for held_actions in folds:
                train_rows = [row for row in target_actions if row["action"] not in held_actions]
                valid_rows = [row for row in target_actions if row["action"] in held_actions]
                sem_tr = np.concatenate([s_by_action[row["action"]] for row in train_rows])
                x_tr = np.concatenate([x_by_action[row["action"]] for row in train_rows])
                sem_va = np.concatenate([s_by_action[row["action"]] for row in valid_rows])
                x_va = np.concatenate([x_by_action[row["action"]] for row in valid_rows])
                sm, ss = sem_tr.mean(axis=0), np.maximum(sem_tr.std(axis=0), 1e-5)
                str_norm, sva_norm = (sem_tr - sm) / ss, (sem_va - sm) / ss
                d_cv = np.linalg.norm(str_norm[:, None, :] - str_norm[None, :, :], axis=2)
                nz_cv = d_cv[d_cv > 1e-10]
                sigma_cv = float(np.median(nz_cv))
                pm, ps, pc = fit_pca(x_tr, args.latent_dimensions)
                ztr = ((x_tr - pm) / ps) @ pc.T
                zscale = np.maximum(ztr.std(axis=0), 1e-6)
                ztrn = ztr / zscale
                centers = farthest_centers(str_norm, min(args.rbf_centers, len(str_norm)))
                gamma_cv = 1.0 / (2.0 * (sigma_cv * width_factor) ** 2)
                model = fit_rbf_ridge(str_norm, ztrn, centers, gamma_cv, alpha)
                zpred = predict_rbf(sva_norm, model) * zscale
                xpred = ((zpred @ pc) * ps) + pm
                feature_rmse = float(np.sqrt(np.mean(((xpred - x_va) /
                    np.maximum(ps, 1e-8)) ** 2)))
                fold_errors.append(feature_rmse)
            cv_rows.append({"width_factor": width_factor, "ridge_alpha": alpha,
                            "fold_feature_rmse": fold_errors,
                            "mean_feature_rmse": float(np.mean(fold_errors))})
    selected = min(cv_rows, key=lambda row: row["mean_feature_rmse"])
    gamma = 1.0 / (2.0 * (sigma * selected["width_factor"]) ** 2)
    centers = farthest_centers(sem_train_norm, min(args.rbf_centers, len(sem_train_norm)))
    regressor = fit_rbf_ridge(sem_train_norm, latent_train_norm, centers, gamma,
                              selected["ridge_alpha"])

    holdout_rows = []
    for row in target_holdouts:
        sem = s_by_action[row["action"]]
        x = x_by_action[row["action"]]
        z_pred = predict_rbf((sem - sem_mean) / sem_std, regressor) * latent_std
        x_pred = ((z_pred @ pca_components) * pca_scale) + pca_mean
        feature_rmse = float(np.sqrt(np.mean(((x_pred - x) / np.maximum(pca_scale, 1e-8)) ** 2)))
        z_true = ((x - pca_mean) / pca_scale) @ pca_components.T
        latent_rmse = float(np.sqrt(np.mean(((z_pred - z_true) / latent_std) ** 2)))
        holdout_rows.append({"action": row["action"], "sample_count": len(x),
            "feature_rmse_standardized": feature_rmse,
            "latent_rmse_standardized": latent_rmse})

    scene = bpy.context.scene
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
        target_copy = duplicate_rig(clean_original, "SemanticWalk_Waluigi_Target_Rig")
        target_copy.matrix_world = baseline_original.matrix_world.copy()
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
        frames = list(range(start, end + 1))
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
        train_distance = np.linalg.norm(sem_train_norm[:, None, :] - source_norm[None, :, :], axis=2)
        nearest_indices = np.argmin(train_distance, axis=0)
        nearest_prior = latent_train_norm[nearest_indices]
        rbf_proposal = predict_rbf(source_norm, regressor)
        proposal, semantic_inverse_costs = solve_semantic_latents(
            source_semantics, nearest_prior, latent_std, pca, bones, target_rest,
            sem_mean, sem_std, native_gap_targets,
            native_weight=args.inverse_native_weight,
            iterations=args.inverse_iterations, initial_proposal=rbf_proposal,
            elbow_weight=args.elbow_weight)
        smoothed = smooth_latent_sequence(proposal, nearest_prior,
            np.ones(args.latent_dimensions),
            lambda_native=args.sequence_native_weight,
            lambda_velocity=args.sequence_velocity_weight,
            lambda_acceleration=args.sequence_acceleration_weight)
        decoded_features = decode_features(smoothed, latent_std, pca)
        pca_pose_rows = [decode_context_poses(row, bones, target_rest)
                         for row in decoded_features]

        # Fit the right semantic stream independently through the same
        # character-agnostic left-profile latent model. Convert its wrist
        # semantic into the canonical model frame, then mirror the resulting
        # per-bone transforms into Waluigi's distinct right rest basis.
        source_norm_right = (source_semantics_right - sem_mean) / sem_std
        distance_right = np.linalg.norm(
            sem_train_norm[:, None, :] - source_norm_right[None, :, :], axis=2)
        nearest_right = latent_train_norm[np.argmin(distance_right, axis=0)]
        rbf_right = predict_rbf(source_norm_right, regressor)
        proposal_right, inverse_costs_right = solve_semantic_latents(
            source_semantics_right, nearest_right, latent_std, pca, bones,
            target_rest, sem_mean, sem_std, native_gap_targets.copy(),
            native_weight=args.inverse_native_weight,
            iterations=args.inverse_iterations, initial_proposal=rbf_right,
            elbow_weight=args.elbow_weight,
            gap_scales=(0.001, 0.0025, 0.0025, 0.0025))
        smoothed_right = smooth_latent_sequence(proposal_right, nearest_right,
            np.ones(args.latent_dimensions),
            lambda_native=args.sequence_native_weight,
            lambda_velocity=args.sequence_velocity_weight,
            lambda_acceleration=args.sequence_acceleration_weight)
        # Temporal smoothing can move a frame away from the strict branch-gap
        # manifold. Reproject the smoothed right sequence independently, using
        # each smoothed frame as its prior and a tighter shoulder/branch gap
        # tolerance; the already-validated left v5 path remains untouched.
        right_gap_scales = (0.001, 0.0025, 0.0025, 0.0025)
        proposal_right, inverse_costs_right = solve_semantic_latents(
            source_semantics_right, smoothed_right, latent_std, pca, bones,
            target_rest, sem_mean, sem_std, native_gap_targets.copy(),
            native_weight=max(args.inverse_native_weight, 0.01),
            iterations=args.inverse_iterations, initial_proposal=smoothed_right,
            elbow_weight=args.elbow_weight, gap_scales=right_gap_scales)
        smoothed_right = proposal_right
        pca_pose_rows_right = [decode_context_poses(row, bones, target_rest)
                               for row in decode_features(smoothed_right, latent_std, pca)]

        mesh_sources = sorted((obj for obj in bpy.data.objects
            if obj.type == "MESH" and obj.name.startswith(args.baseline_mesh_prefix) and
            any(mod.type == "ARMATURE" and mod.object == baseline_original
                for mod in obj.modifiers)), key=lambda obj: obj.name)
        if not mesh_sources:
            raise RuntimeError("validated walk has no complete ARMTEST meshes bound to its rig")
        preview_collection = bpy.data.collections.new("SemanticWalk_Waluigi_LeftArm_Preview")
        bpy.context.scene.collection.children.link(preview_collection)
        collections.append(preview_collection)
        move_to_collection(target_copy, preview_collection)
        target_meshes = [duplicate_mesh(obj, target_copy, preview_collection,
            "SemanticWalk_Waluigi_") for obj in mesh_sources]
        created_objects.extend(target_meshes)
        created_meshes.extend(target_meshes)

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
        target_copy.animation_data.action = None

        combined_pose_rows = []
        basis_samples = []
        combined_gaps = []
        combined_gaps_right = []
        max_pose_scale_delta_vs_walk = 0.0
        semantic_target_rows = []
        semantic_target_right_rows = []
        mesh_finite_failures = []
        arm_bones = set(bones) - TORSO_CONTEXT
        semantic_arm_names = {"bone_22", "bone_23", "bone_24", "bone_25",
                              "bone_26", "bone_27", "bone_29", "bone_30"}
        right_arm_bones = {LEFT_TO_RIGHT_BONES[name] for name in semantic_arm_names}
        right_subtree = descendant_bone_names(target_copy.data, "bone_57") | {"bone_57"}
        written_arm_bones = arm_bones | right_subtree
        right_root_anchor_corrections = []
        right_finger_names = right_subtree - right_arm_bones - {"bone_57"}
        max_right_finger_local_error = 0.0
        max_pose_write_error = 0.0
        max_pose_write_detail = None
        max_foot_baseline_error = 0.0
        max_cap_baseline_error = 0.0
        max_nonmodel_pose_error = 0.0
        for index, frame in enumerate(frames):
            set_frame(scene, float(frame))
            base_pose = walk_pose_rows[index]
            base_scales = walk_scale_rows[index]
            pca_pose = pca_pose_rows[index]
            torso_from = pca_pose[TARGET_PROFILE["torso"]]
            torso_to = numpy_matrix(base_pose[TARGET_PROFILE["torso"]])
            rebase = torso_to @ np.linalg.inv(torso_from)
            mixed_pose = {name: matrix.copy() for name, matrix in base_pose.items()}
            for name in arm_bones:
                mixed_pose[name] = Matrix((rebase @ pca_pose[name]).tolist())
            torso_rest_matrix = numpy_matrix(target_rest[TARGET_PROFILE["torso"]]["rest_matrix"])
            right_profile_pose = pca_pose_rows_right[index]
            for left_name in semantic_arm_names:
                right_name = LEFT_TO_RIGHT_BONES[left_name]
                left_matrix = rebase @ right_profile_pose[left_name]
                right_matrix = mirror_pose_across_sagittal(
                    left_matrix, torso_to, torso_rest_matrix,
                    numpy_matrix(target_rest[left_name]["rest_matrix"]),
                    numpy_matrix(target_rest_all[right_name]["rest_matrix"]))
                mixed_pose[right_name] = Matrix(right_matrix.tolist())
            # The right latent fit can infer a small translation on the
            # unweighted clavicle/root bone. Anchor its head to the validated
            # clean-walk shoulder and carry that translation through every
            # descendant (including non-profile finger bones). This preserves
            # all fitted relative arm/branch poses while preventing shoulder
            # drift and keeping the fingers attached to the glove.
            right_root_delta = (numpy_matrix(base_pose["bone_57"])[0:3, 3] -
                                numpy_matrix(mixed_pose["bone_57"])[0:3, 3])
            right_root_anchor_corrections.append(float(np.linalg.norm(right_root_delta)))
            for name in right_subtree:
                shifted = numpy_matrix(mixed_pose[name]).copy()
                shifted[:3, 3] += right_root_delta
                mixed_pose[name] = Matrix(shifted.tolist())
            # Keep right thumb/finger/helper transforms in their validated
            # baseline parent-local pose while following the new hand and
            # forearm. Rebuild them parent-first so they remain attached.
            def hierarchy_depth(name):
                depth, bone = 0, target_copy.data.bones[name]
                while bone.parent is not None:
                    depth += 1
                    bone = bone.parent
                return depth
            for name in sorted(right_finger_names, key=hierarchy_depth):
                parent_name = target_copy.data.bones[name].parent.name
                relative = (np.linalg.inv(numpy_matrix(base_pose[parent_name])) @
                            numpy_matrix(base_pose[name]))
                mixed_pose[name] = Matrix((numpy_matrix(mixed_pose[parent_name]) @
                                           relative).tolist())
            for name in TORSO_CONTEXT:
                # Keep the validated target walk root/torso channels.
                mixed_pose[name] = base_pose[name].copy()
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
                if error > max_pose_write_error:
                    max_pose_write_error = error
                    max_pose_write_detail = {"frame": frame, "bone": name,
                        "expected": matrix_rows(mixed_pose[name]),
                        "actual": matrix_rows(actual[name])}
            combined_pose_rows.append({name: matrix.copy() for name, matrix in actual.items()})
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
            basis_samples.append((float(frame), bases))

        action_name = "Waluigi_LuigiWalk_SemanticBothArms_v1"
        if bpy.data.actions.get(action_name):
            action_name += "_Prototype"
        baked_action = make_action(target_copy, action_name, basis_samples)
        created_actions.append(baked_action)
        target_copy.animation_data.action = baked_action
        target_copy.animation_data.action_slot = baked_action.slots[0]
        max_baked_replay_error = 0.0
        for index, frame in enumerate(frames):
            set_frame(scene, float(frame))
            baked_pose = evaluated_bone_matrices(target_copy)
            max_baked_replay_error = max(max_baked_replay_error,
                max(matrix_error(baked_pose[name], combined_pose_rows[index][name])
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
        else:
            raise RuntimeError("v1 residual report is required for the improvement gate")
        current_rmse = {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                        for i, name in enumerate(SEMANTIC_NAMES)}
        current_rmse_right = {name: float(np.sqrt(np.mean(semantic_error_right[:, i] ** 2)))
                              for i, name in enumerate(SEMANTIC_NAMES)}
        semantic_groups = {
            "shoulder_orientation": (0, 1, 2), "elbow_flexion": (3,),
            "forearm_roll": (4,), "wrist_orientation": (5, 6, 7),
            "hand_position": (8, 9, 10)}
        group_comparison = {}
        for label, indices in semantic_groups.items():
            names = [SEMANTIC_NAMES[i] for i in indices]
            before = math.sqrt(sum(float(v1_rmse[name]) ** 2 for name in names) / len(names))
            after = math.sqrt(sum(current_rmse[name] ** 2 for name in names) / len(names))
            group_comparison[label] = {"v1_rmse": before, "current_rmse": after,
                "relative_improvement": (before - after) / max(before, 1e-12)}
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
        squared_vel = np.diff(smoothed, axis=0)
        squared_acc = np.diff(smoothed, n=2, axis=0)
        objective_terms = {
            "semantic_anchor_mse": float(np.mean((smoothed - proposal) ** 2)),
            "native_pose_prior_mse": float(np.mean((smoothed - nearest_prior) ** 2)),
            "velocity_mse": float(np.mean(squared_vel ** 2)),
            "acceleration_mse": float(np.mean(squared_acc ** 2)),
            "lambdas": {"native": args.sequence_native_weight,
                "velocity": args.sequence_velocity_weight,
                "acceleration": args.sequence_acceleration_weight}}
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
                "standardized_residual_weight": float(args.elbow_weight),
                "target_native_training_range_radians": [float(sem_train[:, 3].min()),
                                                          float(sem_train[:, 3].max())]},
            "right_arm_mapping": {
                "source_bones": {key: SOURCE_PROFILE_RIGHT[key] for key in
                    ("shoulder", "upper", "elbow_a", "elbow_b", "forearm", "forearm_overlap", "wrist", "hand")},
                "target_bones": {key: TARGET_PROFILE_RIGHT[key] for key in
                    ("shoulder", "upper", "elbow_a", "elbow_b", "forearm", "forearm_overlap", "wrist", "hand")},
                "left_to_right_deform_bone_map": LEFT_TO_RIGHT_BONES,
                "method": "independent right-source semantic solve through shared context PCA, followed by rest-calibrated sagittal reflection into the homologous right basis",
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
                "standardized_residual_weight": float(args.elbow_weight)},
            "source_target_semantic_rmse": {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                                              for i, name in enumerate(SEMANTIC_NAMES)},
            "right_source_target_semantic_rmse": current_rmse_right,
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
            "target_model": {"type": "PCA latent context with direct semantic inverse; RBF ridge CV baseline",
                "latent_dimensions": args.latent_dimensions, "feature_dimensions": int(x_train.shape[1]),
                "training_actions": [row["action"] for row in target_actions],
                "training_samples": int(len(x_train)), "holdout_actions_validation_only": [row["action"] for row in target_holdouts],
                "holdout_samples": int(sum(len(row["samples"]) for row in target_holdouts)),
                "rbf_centers": int(len(centers)), "selected_width_factor": selected["width_factor"],
                "selected_ridge_alpha": selected["ridge_alpha"], "sigma_median": sigma,
                "grouped_cv": cv_rows, "native_holdout_validation": holdout_rows},
            "optimization": {"objective_terms": objective_terms,
                "per_frame_semantic_inverse_objective": {
                    "form": "||normalized semantic residual||^2 + ||native joint-gap residual / tolerance||^2 + lambda_native||z-nearest_native||^2",
                    "mean": float(np.mean(semantic_inverse_costs)),
                    "max": float(np.max(semantic_inverse_costs)),
                    "native_joint_gap_targets_armature_units": {name: float(native_gap_targets[i]) for i, name in enumerate(GAP_PAIRS)},
                    "joint_gap_scales_armature_units": {name: value for name, value in zip(GAP_PAIRS, (0.01, 0.005, 0.005, 0.005))},
                    "native_weight": args.inverse_native_weight,
                    "iterations_max": args.inverse_iterations,
                    "initialization": "nearest native latent pose"},
                "rbf_baseline_proposal_rms_from_inverse": float(np.sqrt(np.mean((rbf_proposal - proposal) ** 2))),
                "latent_coefficients_normalized": {"min": float(smoothed.min()), "max": float(smoothed.max()),
                    "rms": float(np.sqrt(np.mean(smoothed ** 2)))},
                "max_nearest_native_prior_distance": float(np.max(np.linalg.norm(smoothed - nearest_prior, axis=1))),
                "max_step": float(np.max(np.linalg.norm(np.diff(smoothed, axis=0), axis=1))),
                "max_acceleration": float(np.max(np.linalg.norm(np.diff(smoothed, n=2, axis=0), axis=1)))},
            "preview": {"file": os.path.abspath(args.preview), "collection": preview_collection.name,
                "root": None, "rig": target_copy.name, "action": baked_action.name,
                "mesh_objects": [obj.name for obj in target_meshes],
                "object_scale_unchanged": [float(v) for v in target_copy.scale],
                "rig_world_matrix": matrix_rows(target_copy.matrix_world),
                "constraints_removed_from_duplicate": cleared_constraints,
                "runtime_constraints_remaining": sum(len(obj.constraints) +
                    sum(len(pb.constraints) for pb in obj.pose.bones) for obj in (target_copy,)),
                "max_pose_write_matrix_error": max_pose_write_error,
                "max_pose_write_divergence": max_pose_write_detail,
                "max_pose_scale_delta_vs_validated_walk": max_pose_scale_delta_vs_walk,
                "max_nonmodeled_bone_pose_matrix_error_vs_walk": max_nonmodel_pose_error,
                "max_foot_tail_error_vs_walk": max_foot_baseline_error,
                "max_head_cap_centroid_error_vs_walk": max_cap_baseline_error,
                "max_baked_action_replay_matrix_error": max_baked_replay_error,
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
                       "target_latent_action_is_new": True}}

        report["validation"] = {
            "fit_gate": "pass" if not mesh_finite_failures and max_pose_write_error < 2e-3 and
                         max_baked_replay_error < 1e-4 and
                         max_nonmodel_pose_error < 2e-3 and
                         max_right_finger_local_error < 2e-3 and
                         max_foot_baseline_error < 2e-4 and
                         max_cap_baseline_error < 2e-4 and
                         all(value < 0.02 for value in native_gap_outside_range.values()) and
                         all(value < 0.02 for value in native_gap_outside_range_right.values()) else "fail",
            "semantic_fit_gate": "pass" if all(
                row["relative_improvement"] >= 0.25 for row in group_comparison.values()) else "fail",
            "max_pose_write_matrix_tolerance": 2e-3,
            "max_baked_action_replay_matrix_tolerance": 1e-4,
            "max_nonmodeled_pose_error_tolerance": 2e-3,
            "max_foot_cap_baseline_error_tolerance": 2e-4,
            "max_joint_gap_outside_native_range_tolerance_armature_units": 0.02,
            "finite_pose_mesh_values": "pass" if not mesh_finite_failures else "fail"}

        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
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
            if preview_collection not in obj.users_collection:
                raise RuntimeError(f"preview mesh {obj.name} is not linked to its preview collection")
        bpy.context.view_layer.objects.active = target_copy
        target_copy.select_set(True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
        print("MG1_SEMANTIC_WALK_FIT=" + json.dumps({
            "preview": report["preview"]["file"], "action": baked_action.name,
            "validation": report["validation"],
            "semantic_rmse": report["preview"]["semantic_feature_rmse_after_solve"],
            "selected_rbf": {"width": selected["width_factor"], "alpha": selected["ridge_alpha"]}}))
        if report["validation"]["fit_gate"] != "pass":
            raise RuntimeError("semantic walk preview failed numerical bake validation")
        if report["validation"]["semantic_fit_gate"] != "pass":
            raise RuntimeError("semantic feature residuals did not improve by 25% in every group")
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
