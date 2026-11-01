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
from build_context_pca_previews import (
    associated_objects, duplicate_character_objects, make_transform_root,
    move_to_collection, parent_rig_to_transform_root,
)
from convert_native_owner_actions import (
    basis_from_pose, duplicate_rig, evaluated_bone_matrices, make_action, set_frame,
)
from fit_native_context_pca import fit as fit_pca
from semantic_arm_fit_math import (
    encode_context_sample, farthest_centers, fit_rbf_ridge,
    predict_rbf, semantic_features, smooth_latent_sequence,
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
    parser.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--walk-slot", default="", help="optional explicit target walk action slot")
    parser.add_argument("--walk-curves-json", default="",
                        help="read-only curve export for a live walk action absent from the input .blend")
    parser.add_argument("--frames", nargs=2, type=int, default=(0, 60))
    parser.add_argument("--latent-dimensions", type=int, default=192)
    parser.add_argument("--rbf-centers", type=int, default=72)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--report", required=True)
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


def semantic_from_source_sample(rest, pose, body_map, forearm_map):
    return semantic_features(rest, pose, SOURCE_PROFILE, body_map, forearm_map)


def semantic_from_target_sample(rest, pose):
    return semantic_features(rest, pose, TARGET_PROFILE)


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
                           native_gap_targets, native_weight=0.2, iterations=6):
    """Invert the PCA pose manifold for semantic targets, regularized to native poses."""
    targets = np.c_[(np.asarray(source_semantics, dtype=np.float64) - sem_mean) / sem_std,
                     np.zeros((len(source_semantics), len(GAP_PAIRS)), dtype=np.float64)]
    priors = np.asarray(native_prior, dtype=np.float64)
    solutions, costs = [], []
    dim = priors.shape[1]
    for target, prior in zip(targets, priors):
        z = prior.copy()

        def residual(value):
            return target_semantics_from_latent(value, latent_std, pca_model,
                bones, rest_bones, sem_mean, sem_std, native_gap_targets,
                np.asarray((0.01, 0.005, 0.005, 0.005))) - target

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
    source_action = bpy.data.actions.get(args.source_action)
    walk_action = bpy.data.actions.get(args.walk_action)
    walk_curve_payload = None
    if walk_action is None and args.walk_curves_json:
        with open(args.walk_curves_json, "r", encoding="utf-8") as stream:
            walk_curve_payload = json.load(stream)
        if walk_curve_payload.get("action") != args.walk_action:
            raise RuntimeError("walk curve export action name differs from requested action")
    if not source_rig or source_rig.type != "ARMATURE" or not target_original or target_original.type != "ARMATURE":
        raise RuntimeError("source Luigi or target Waluigi armature is missing")
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
    if not source_profile_names.issubset(set(source_rig.data.bones.keys())):
        raise RuntimeError("source Luigi profile bones are missing: " + repr(sorted(source_profile_names - set(source_rig.data.bones.keys()))))
    if not set(bones).issubset(set(target_original.data.bones.keys())):
        raise RuntimeError("target Waluigi is missing context profile bones")
    if abs(float(target_original.scale[0]) - 43.437) > 0.01:
        raise RuntimeError("production Waluigi scale changed; refusing to proceed")
    start, end = args.frames
    if (start, end) != (0, 60) or tuple(float(v) for v in source_action.frame_range) != (0.0, 60.0):
        raise RuntimeError("this prototype is limited to the Luigi walk's frames 0–60")

    target_rest = target_rest_from_dataset(dataset)
    source_rest = source_rest_from_rig(source_rig)
    body_map = frame_map(source_rig, source_rest, SOURCE_PROFILE["torso"],
                         target_original, target_rest, TARGET_PROFILE["torso"])
    forearm_map = frame_map(source_rig, source_rest, SOURCE_PROFILE["forearm"],
                            target_original, target_rest, TARGET_PROFILE["forearm"])

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
    created_objects, created_meshes, created_actions = [], [], []
    target_copy = None
    collections = []
    try:
        target_copy = duplicate_rig(target_original, "SemanticWalk_Waluigi_Target_Rig")
        created_objects.append(target_copy)
        cleared_constraints = clear_constraints(target_copy)
        if walk_action is None:
            walk_action, walk_slot = import_walk_curve_action(walk_curve_payload, target_copy)
            created_actions.append(walk_action)
        elif walk_slot is None:
            walk_slot = select_walk_slot(walk_action, args.walk_slot,
                                         target_original.data.bones.keys())
        source_semantics = []
        frames = list(range(start, end + 1))
        for frame in frames:
            set_frame(scene, float(frame))
            pose, _basis = source_pose_from_curves(source_rig, source_action,
                                                   source_slot, float(frame))
            pose_np = {name: numpy_matrix(matrix) for name, matrix in pose.items()}
            source_semantics.append(semantic_from_source_sample(source_rest, pose_np,
                                                                  body_map, forearm_map))
        source_semantics = np.asarray(source_semantics, dtype=np.float64)
        source_norm = (source_semantics - sem_mean) / sem_std
        train_distance = np.linalg.norm(sem_train_norm[:, None, :] - source_norm[None, :, :], axis=2)
        nearest_indices = np.argmin(train_distance, axis=0)
        nearest_prior = latent_train_norm[nearest_indices]
        rbf_proposal = predict_rbf(source_norm, regressor)
        proposal, semantic_inverse_costs = solve_semantic_latents(
            source_semantics, nearest_prior, latent_std, pca, bones, target_rest,
            sem_mean, sem_std, native_gap_targets, native_weight=0.2, iterations=6)
        smoothed = smooth_latent_sequence(proposal, nearest_prior,
            np.ones(args.latent_dimensions), lambda_native=0.2,
            lambda_velocity=0.05, lambda_acceleration=0.5)
        decoded_features = decode_features(smoothed, latent_std, pca)
        pca_pose_rows = [decode_context_poses(row, bones, target_rest)
                         for row in decoded_features]

        target_objects = associated_objects(target_original)
        # Earlier arm experiments left helper meshes bound to the production
        # rig. They are not part of the character and must not enter preview.
        target_objects = [obj for obj in target_objects
                          if not obj.name.startswith("ARMTEST_") and
                          "_ArmRetargetPreview" not in obj.name]
        mesh_sources = [obj for obj in target_objects if obj.type == "MESH"]
        preview_collection = bpy.data.collections.new("SemanticWalk_Waluigi_LeftArm_Preview")
        bpy.context.scene.collection.children.link(preview_collection)
        collections.append(preview_collection)
        root = make_transform_root("SemanticWalk_Waluigi_Root", preview_collection,
                                   target_original.matrix_world)
        created_objects.append(root)
        parent_rig_to_transform_root(target_copy, root)
        move_to_collection(target_copy, preview_collection)
        target_meshes = duplicate_character_objects(mesh_sources, target_original,
            target_copy, preview_collection, "SemanticWalk_Waluigi_")
        created_objects.extend(target_meshes)
        created_meshes.extend(target_meshes)

        target_copy.animation_data_create()
        target_copy.animation_data.action = walk_action
        target_copy.animation_data.action_slot = walk_slot
        # Snapshot the validated walk completely before manual matrix writes.
        # Leaving its action active would let animated channels overwrite
        # matrix_basis assignments during dependency-graph evaluation.
        walk_pose_rows, walk_scale_rows, baseline_gaps = [], [], []
        for frame in frames:
            set_frame(scene, float(frame))
            walk_pose_rows.append({name: matrix.copy()
                                   for name, matrix in evaluated_bone_matrices(target_copy).items()})
            walk_scale_rows.append({pb.name: np.asarray(tuple(pb.scale), dtype=np.float64)
                                    for pb in target_copy.pose.bones})
            baseline_gaps.append(continuity(target_copy))
        target_copy.animation_data.action = None

        combined_pose_rows = []
        basis_samples = []
        combined_gaps = []
        max_pose_scale_delta_vs_walk = 0.0
        semantic_target_rows = []
        mesh_finite_failures = []
        arm_bones = set(bones) - TORSO_CONTEXT
        max_pose_write_error = 0.0
        max_pose_write_detail = None
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
            for name in TORSO_CONTEXT:
                # Keep the validated target walk root/torso channels.
                mixed_pose[name] = base_pose[name].copy()
            bases = assign_pose_matrices_hierarchical(target_copy, mixed_pose)
            actual = evaluated_bone_matrices(target_copy)
            max_pose_scale_delta_vs_walk = max(max_pose_scale_delta_vs_walk,
                max(float(np.max(np.abs(np.asarray(tuple(pb.scale), dtype=np.float64) -
                                         base_scales[pb.name])))
                    for pb in target_copy.pose.bones))
            for name in actual:
                error = matrix_error(actual[name], mixed_pose[name])
                if error > max_pose_write_error:
                    max_pose_write_error = error
                    max_pose_write_detail = {"frame": frame, "bone": name,
                        "expected": matrix_rows(mixed_pose[name]),
                        "actual": matrix_rows(actual[name])}
            combined_pose_rows.append({name: matrix.copy() for name, matrix in actual.items()})
            combined_gaps.append(continuity(target_copy))
            target_semantics = semantic_from_target_sample(target_rest,
                {name: numpy_matrix(actual[name]) for name in bones})
            semantic_target_rows.append(target_semantics)
            failures = finite_pose(target_copy)
            if failures:
                mesh_finite_failures.extend({"frame": frame, "bone": item} for item in failures)
            for mesh_name, vertices in mesh_vertices(target_meshes).items():
                for vertex_index, point in enumerate(vertices):
                    if not all(math.isfinite(float(v)) for v in point):
                        mesh_finite_failures.append({"frame": frame, "mesh": mesh_name,
                                                     "vertex": vertex_index})
            basis_samples.append((float(frame), bases))

        action_name = "Waluigi_LuigiWalk_SemanticLeftArm_v1"
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
        semantic_error = np.asarray(semantic_target_rows) - source_semantics
        squared_vel = np.diff(smoothed, axis=0)
        squared_acc = np.diff(smoothed, n=2, axis=0)
        objective_terms = {
            "semantic_anchor_mse": float(np.mean((smoothed - proposal) ** 2)),
            "native_pose_prior_mse": float(np.mean((smoothed - nearest_prior) ** 2)),
            "velocity_mse": float(np.mean(squared_vel ** 2)),
            "acceleration_mse": float(np.mean(squared_acc ** 2)),
            "lambdas": {"native": 0.2, "velocity": 0.05, "acceleration": 0.5}}
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
            "schema": "mg1.semantic-luigi-walk-to-waluigi-left-arm.v1",
            "source_rig": source_rig.name, "source_action": source_action.name,
            "source_slot": source_slot.identifier, "source_frames": frames,
            "target_rig": target_original.name, "target_walk_action": walk_action.name,
                "target_walk_slot": walk_slot.identifier,
                "target_walk_curves_imported_from_live_bridge": walk_curve_payload is not None,
                "target_bones": bones,
            "semantic_feature_names": list(SEMANTIC_NAMES),
            "source_target_semantic_rmse": {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                                              for i, name in enumerate(SEMANTIC_NAMES)},
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
                    "form": "||normalized semantic residual||^2 + ||native joint-gap residual / tolerance||^2 + 0.2||z-nearest_native||^2",
                    "mean": float(np.mean(semantic_inverse_costs)),
                    "max": float(np.max(semantic_inverse_costs)),
                    "native_joint_gap_targets_armature_units": {name: float(native_gap_targets[i]) for i, name in enumerate(GAP_PAIRS)},
                    "joint_gap_scales_armature_units": {name: value for name, value in zip(GAP_PAIRS, (0.01, 0.005, 0.005, 0.005))},
                    "iterations_max": 6,
                    "initialization": "nearest native latent pose"},
                "rbf_baseline_proposal_rms_from_inverse": float(np.sqrt(np.mean((rbf_proposal - proposal) ** 2))),
                "latent_coefficients_normalized": {"min": float(smoothed.min()), "max": float(smoothed.max()),
                    "rms": float(np.sqrt(np.mean(smoothed ** 2)))},
                "max_nearest_native_prior_distance": float(np.max(np.linalg.norm(smoothed - nearest_prior, axis=1))),
                "max_step": float(np.max(np.linalg.norm(np.diff(smoothed, axis=0), axis=1))),
                "max_acceleration": float(np.max(np.linalg.norm(np.diff(smoothed, n=2, axis=0), axis=1)))},
            "preview": {"file": os.path.abspath(args.preview), "collection": preview_collection.name,
                "root": root.name, "rig": target_copy.name, "action": baked_action.name,
                "mesh_objects": [obj.name for obj in target_meshes],
                "object_scale_unchanged": [float(v) for v in target_copy.scale],
                "constraints_removed_from_duplicate": cleared_constraints,
                "runtime_constraints_remaining": sum(len(obj.constraints) +
                    sum(len(pb.constraints) for pb in obj.pose.bones) for obj in (target_copy,)),
                "max_pose_write_matrix_error": max_pose_write_error,
                "max_pose_write_divergence": max_pose_write_detail,
                "max_pose_scale_delta_vs_validated_walk": max_pose_scale_delta_vs_walk,
                "max_baked_action_replay_matrix_error": max_baked_replay_error,
                "walk_root_world_matrix": matrix_rows(root.matrix_world),
                "max_joint_gap_change_vs_validated_walk": baseline_gap_change,
                "native_training_joint_gap_ranges_armature_units": native_gap_ranges,
                "max_joint_gap_outside_native_range_armature_units": native_gap_outside_range,
                "semantic_feature_rmse_after_solve": {name: float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                                                        for i, name in enumerate(SEMANTIC_NAMES)},
                "finite_value_failures": mesh_finite_failures,
                "sampled_frames_for_visual_review": [0, 15, 30, 45, 60]},
            "safety": {"production_objects_mutated": False, "production_actions_mutated": False,
                       "source_action_assigned_to_duplicate": False,
                       "target_latent_action_is_new": True}}

        report["validation"] = {
            "fit_gate": "pass" if not mesh_finite_failures and max_pose_write_error < 2e-3 and
                         max_baked_replay_error < 1e-4 and
                         all(value < 0.02 for value in native_gap_outside_range.values()) else "fail",
            "semantic_fit_gate": "pass" if max(float(np.sqrt(np.mean(semantic_error[:, i] ** 2)))
                                                  for i in range(len(SEMANTIC_NAMES))) < 0.3 else "fail",
            "max_pose_write_matrix_tolerance": 2e-3,
            "max_baked_action_replay_matrix_tolerance": 1e-4,
            "max_joint_gap_outside_native_range_tolerance_armature_units": 0.02,
            "finite_pose_mesh_values": "pass" if not mesh_finite_failures else "fail"}

        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
        for obj in old_objects:
            obj.hide_set(True)
        for obj in created_objects:
            obj.hide_set(False)
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
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        # Preserve the saved preview file, but clean temporary data from this
        # background session. No input .blend is saved.
        for obj in list(bpy.data.objects):
            if obj in old_objects:
                try:
                    obj.hide_set(old_obj_visibility[obj])
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
