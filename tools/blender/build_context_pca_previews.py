"""Build full-character, native-context overlays for PCA holdout actions.

Each output is a separate Blender file containing a full converted native
reference and an overlaid copy where only profile bones use PCA transforms.
Production objects/actions are read-only; every preview object is disposable.
"""
import argparse
import gzip
import json
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import (
    basis_from_pose, converted_matrices, duplicate_rig, evaluated_bone_matrices,
    make_action, set_frame,
)
from analyze_native_arm_context import source_pose_from_curves
from fit_native_context_pca import fit as fit_pca, predict as pca_predict
from context_dataset import normalize_context_dataset

DEFAULT_ARM_REGION = {f"bone_{n}" for n in (22, 23, 24, 25, 26, 27, 29, 30)}


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--preview-dir", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--source-rig", default="Armature.001")
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    p.add_argument("--slot-id", default="OBArmature.004")
    p.add_argument("--raw-anm-source", action="store_true",
                   help="evaluate official imported Sluggies actions directly on the matching .sluggie rig")
    p.add_argument("--max-overlay-error", type=float, default=0.01)
    p.add_argument("--latent-dimensions", type=int, default=192)
    p.add_argument("--action", help="optional single holdout for focused diagnostics")
    return p.parse_args(argv)


def action_bag(action, slot):
    bags = [bag for layer in action.layers for strip in layer.strips
            for bag in getattr(strip, "channelbags", []) if bag.slot == slot]
    if len(bags) != 1:
        raise RuntimeError(f"{action.name}: expected one channelbag for {slot.identifier}")
    return bags[0]


def all_action_times(action, slot):
    bag = action_bag(action, slot)
    keys = sorted({float(point.co.x) for curve in bag.fcurves
                   if curve.data_path.startswith("pose.bones[")
                   for point in curve.keyframe_points})
    times = set(keys + [float(v) for v in action.frame_range])
    times.update((a + b) * 0.5 for a, b in zip(keys, keys[1:]))
    return sorted(times)


def move_to_collection(obj, collection):
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    collection.objects.link(obj)


def make_transform_root(name, collection, world_matrix):
    root = bpy.data.objects.new(name, None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 10.0
    collection.objects.link(root)
    root.matrix_world = world_matrix.copy()
    return root


def parent_rig_to_transform_root(rig, root):
    rig.parent = root
    rig.matrix_parent_inverse = Matrix.Identity(4)
    rig.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()


def associated_objects(target):
    """All directly bound meshes plus recursively parented accessories."""
    selected = {obj for obj in bpy.data.objects if obj.type == "MESH" and any(
        modifier.type == "ARMATURE" and modifier.object == target
        for modifier in obj.modifiers)}
    changed = True
    while changed:
        changed = False
        for obj in bpy.data.objects:
            if obj not in selected and obj.parent in selected | {target}:
                selected.add(obj)
                changed = True
    return sorted(selected, key=lambda obj: (obj.parent is not None, obj.name))


def duplicate_character_objects(sources, original_rig, rig, collection, prefix):
    copies = {}
    pending = list(sources)
    while pending:
        progressed = False
        for src in list(pending):
            if src.parent and src.parent != original_rig and src.parent not in copies:
                continue
            dup = src.copy()
            if src.data:
                dup.data = src.data.copy()
                dup.data.name = prefix + src.data.name
            dup.name = prefix + src.name
            bpy.context.scene.collection.objects.link(dup)
            move_to_collection(dup, collection)
            if src.parent == original_rig:
                dup.parent = rig
            elif src.parent in copies:
                dup.parent = copies[src.parent]
            dup.parent_type = src.parent_type
            dup.parent_bone = src.parent_bone
            dup.matrix_world = src.matrix_world.copy()
            for modifier in dup.modifiers:
                if modifier.type == "ARMATURE" and modifier.object == original_rig:
                    modifier.object = rig
            for constraint in dup.constraints:
                if hasattr(constraint, "target") and constraint.target == original_rig:
                    constraint.target = rig
                elif hasattr(constraint, "target") and constraint.target in copies:
                    constraint.target = copies[constraint.target]
            copies[src] = dup
            pending.remove(src)
            progressed = True
        if not progressed:
            raise RuntimeError("could not duplicate accessory parent hierarchy")
    return [copies[src] for src in sources]


def feature_quaternion(values):
    vector = Vector((float(values[0]), float(values[1]), float(values[2])))
    angle = vector.length
    return Quaternion(vector / angle, angle) if angle > 1e-10 else Quaternion()


def interpolate_prediction(times, rows, time, bone_count):
    """Interpolate translations linearly and rotations on SO(3)."""
    if time <= times[0]:
        return rows[0]
    if time >= times[-1]:
        return rows[-1]
    hi = next(i for i, value in enumerate(times) if value >= time)
    if abs(times[hi] - time) < 1e-7:
        return rows[hi]
    lo = hi - 1
    alpha = (time - times[lo]) / (times[hi] - times[lo])
    out = np.empty(rows.shape[1], dtype=np.float64)
    for index in range(bone_count):
        start = index * 6
        out[start:start+3] = rows[lo, start:start+3] * (1-alpha) + rows[hi, start:start+3] * alpha
        q = feature_quaternion(rows[lo, start+3:start+6]).slerp(
            feature_quaternion(rows[hi, start+3:start+6]), alpha)
        axis, angle = q.to_axis_angle()
        out[start+3:start+6] = np.asarray(axis, dtype=np.float64) * float(angle)
    return out


def decode_context(feature, bones, rest_bones):
    poses = {}
    for index, name in enumerate(bones):
        row = feature[index*6:index*6+6]
        translation = Vector((float(row[0]), float(row[1]), float(row[2])))
        rotation = feature_quaternion(row[3:6])
        delta = Matrix.LocRotScale(translation, rotation, Vector((1.0, 1.0, 1.0)))
        poses[name] = Matrix(rest_bones[name]["rest_matrix"]) @ delta
    return poses


def encode_native_context(poses, bones, rest_bones):
    feature = []
    for name in bones:
        rest = Matrix(rest_bones[name]["rest_matrix"])
        delta = rest.inverted_safe() @ poses[name]
        translation, rotation, scale = delta.decompose()
        if max(abs(float(v) - 1.0) for v in scale) > 2e-4:
            raise RuntimeError(f"non-unit context scale while encoding {name}: {tuple(scale)}")
        q = rotation.normalized()
        if q.w < 0:
            q.negate()
        axis, angle = q.to_axis_angle()
        rotvec = Vector(axis) * float(angle)
        feature.extend([float(v) for v in translation])
        feature.extend([float(v) for v in rotvec])
    return np.asarray(feature, dtype=np.float64)


def fit_training_pca(dataset, latent_dimensions):
    train = [sample for action in dataset["actions"] if action["split"] == "train"
             for sample in action["samples"]]
    features = np.asarray([encode_sample_for_fit(sample, dataset["bones"]) for sample in train],
                          dtype=np.float64)
    if latent_dimensions >= min(features.shape):
        raise ValueError("latent dimensions must be below both sample count and feature width")
    return fit_pca(features, latent_dimensions)


def bound_action_slot(action, requested_id=None):
    slots = [slot for layer in action.layers for strip in layer.strips
             for bag in getattr(strip, "channelbags", []) for slot in (bag.slot,)]
    unique = {slot.identifier: slot for slot in slots}
    if requested_id:
        slot = unique.get(requested_id)
        if slot is None:
            raise RuntimeError(f"{action.name}: channelbag slot {requested_id!r} is absent")
        return slot
    if len(unique) != 1:
        raise RuntimeError(f"{action.name}: expected one raw action slot, found {sorted(unique)}")
    return next(iter(unique.values()))


def encode_sample_for_fit(sample, bones):
    """Match the training feature encoder: translation plus sign-invariant SO(3) log."""
    import math
    row = []
    for name in bones:
        tr = sample["bones"][name]["rest_relative_se3"]
        q = np.asarray(tr["rotation_quaternion"], dtype=np.float64)
        q /= np.linalg.norm(q)
        if q[0] < 0:
            q = -q
        xyz = q[1:]
        length = float(np.linalg.norm(xyz))
        angle = 2.0 * math.atan2(length, float(q[0]))
        rotvec = xyz * (angle / length) if length > 1e-10 else 2.0 * xyz
        row.extend(tr["translation"])
        row.extend(rotvec.tolist())
    return row


def set_pose_matrices(rig, poses):
    bases = basis_from_pose(rig, poses)
    for name, matrix in bases.items():
        pb = rig.pose.bones[name]
        pb.rotation_mode = "QUATERNION"
        pb.matrix_basis = matrix
    bpy.context.view_layer.update()
    return bases


def evaluated_mesh_vertices(mesh_objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = {}
    for obj in mesh_objects:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            result[obj.name] = [ev.matrix_world @ vertex.co for vertex in mesh.vertices]
        finally:
            ev.to_mesh_clear()
    return result


def mesh_overlay_error(native, pca):
    worst = {"error_world": 0.0}
    sq, count = 0.0, 0
    for (mesh_name, vertices), (_pca_name, other) in zip(native.items(), pca.items()):
        if len(vertices) != len(other):
            raise RuntimeError(f"vertex count mismatch in {mesh_name}")
        for index, (a, b) in enumerate(zip(vertices, other)):
            err = float((a - b).length)
            sq += err * err
            count += 1
            if err > worst["error_world"]:
                worst = {"error_world": err, "mesh": mesh_name, "vertex": index}
    return {"vertex_count": count, "rmse_world": (sq / max(1, count)) ** 0.5, **worst}


def clean_preview_ids(objects, actions, collections):
    for obj in objects:
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    for action in actions:
        if action.name in bpy.data.actions:
            bpy.data.actions.remove(action, do_unlink=True)
    for collection in collections:
        if collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)


def main():
    args = args_parse()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = normalize_context_dataset(json.load(stream))
    source_original = bpy.data.objects.get(args.source_rig)
    target_original = bpy.data.objects.get(args.target_rig)
    if source_original is None or target_original is None:
        raise RuntimeError("source/target rigs are missing from the input .blend")
    if args.raw_anm_source and source_original != target_original:
        raise RuntimeError("raw .anm evaluation requires the matching source and target .sluggie rig")
    source_objects = associated_objects(target_original)
    mesh_sources = [obj for obj in source_objects if obj.type == "MESH"]
    profile_bones = list(dataset["bones"])
    model = fit_training_pca(dataset, args.latent_dimensions)
    holdouts = [row for row in dataset["actions"] if row["split"] == "holdout"
                and (not args.action or row["action"] == args.action)]
    actions_by_name = {action.name: action for action in bpy.data.actions}
    conversion = target_original.matrix_world.inverted_safe() @ source_original.matrix_world
    original_objects = set(bpy.data.objects)
    original_actions = set(bpy.data.actions)
    original_collections = set(bpy.data.collections)
    original_visibility = {obj: obj.hide_get() for obj in original_objects}
    os.makedirs(args.preview_dir, exist_ok=True)
    report_actions = []
    try:
        for action_row in holdouts:
            name = action_row["action"]
            source_action = actions_by_name.get(name)
            if source_action is None:
                raise RuntimeError(f"action {name!r} is missing from the preview source .blend")
            source_slot = bound_action_slot(source_action,
                None if args.raw_anm_source else args.slot_id)
            data_rows = action_row["samples"]
            source_times = all_action_times(source_action, source_slot)

            native_collection = bpy.data.collections.new("NativeReference_" + name)
            pca_collection = bpy.data.collections.new("PCAOverlay_" + name)
            bpy.context.scene.collection.children.link(native_collection)
            bpy.context.scene.collection.children.link(pca_collection)
            preview_objects, preview_actions = [], []

            native_rig = duplicate_rig(target_original, "NativeReference_" + name + "_Rig")
            pca_rig = duplicate_rig(target_original, "PCAOverlay_" + name + "_Rig")
            move_to_collection(native_rig, native_collection)
            move_to_collection(pca_rig, pca_collection)
            # Keep the intentional root transform and 43.437 scale on a parent
            # empty. Action-slot evaluation can reset an armature object's own
            # unkeyed object transform when a layered action is reopened.
            native_root = make_transform_root("NativeReference_" + name + "_Root",
                native_collection, target_original.matrix_world)
            pca_root = make_transform_root("PCAOverlay_" + name + "_Root",
                pca_collection, target_original.matrix_world)
            parent_rig_to_transform_root(native_rig, native_root)
            parent_rig_to_transform_root(pca_rig, pca_root)
            bpy.context.view_layer.update()
            native_meshes = duplicate_character_objects(mesh_sources, target_original,
                native_rig, native_collection, "NativeReference_" + name + "_")
            pca_meshes = duplicate_character_objects(mesh_sources, target_original,
                pca_rig, pca_collection, "PCAOverlay_" + name + "_")
            preview_objects.extend([native_rig, pca_rig, native_root, pca_root]
                                   + native_meshes + pca_meshes)

            native_samples, pca_samples = [], []
            overlay_max, overlay_sq, overlay_count = 0.0, 0.0, 0
            worst_overlay = {"error_world": 0.0}
            context_basis_max = native_basis_max = 0.0
            native_pca_context_max = 0.0
            unmodeled_native_pca_max = 0.0
            dataset_native_context_max = 0.0
            dataset_native_context_worst = {"max_matrix_error": 0.0}
            dataset_times = {float(sample["time"]): sample for sample in data_rows}
            per_sample = []
            for time in source_times:
                set_frame(bpy.context.scene, time)
                if args.raw_anm_source:
                    source_original.animation_data_create()
                    source_original.animation_data.action = source_action
                    source_original.animation_data.action_slot = source_slot
                    source_original.animation_data.action_influence = 1.0
                    source_original.animation_data.action_blend_type = "REPLACE"
                    bpy.context.view_layer.update()
                    native_poses = evaluated_bone_matrices(source_original)
                else:
                    # Legacy converted-action comparison mode. Training itself
                    # is still restricted to raw .anm datasets by the loader.
                    source_pose, _source_basis = source_pose_from_curves(
                        source_original, source_action, source_slot, time)
                    native_poses = converted_matrices(source_original, target_original,
                                                      source_pose, conversion)
                native_basis = set_pose_matrices(native_rig, native_poses)
                evaluated_native = evaluated_bone_matrices(native_rig)
                native_error = max(max(abs(float(evaluated_native[bone][r][c]) - float(native_poses[bone][r][c]))
                    for r in range(4) for c in range(4)) for bone in native_poses)
                native_basis_max = max(native_basis_max, native_error)

                native_feature = encode_native_context(native_poses, profile_bones,
                                                        dataset["rest_bones"])
                predicted = pca_predict(native_feature.reshape(1, -1), model)[0]
                pca_poses = {bone: matrix.copy() for bone, matrix in native_poses.items()}
                pca_poses.update(decode_context(predicted, profile_bones, dataset["rest_bones"]))
                # Retain the full native action on every bone outside the model.
                pca_basis = set_pose_matrices(pca_rig, pca_poses)
                evaluated_pca = evaluated_bone_matrices(pca_rig)
                for bone in profile_bones:
                    context_basis_max = max(context_basis_max,
                        max(abs(float(evaluated_pca[bone][r][c]) - float(pca_poses[bone][r][c]))
                            for r in range(4) for c in range(4)))
                    native_pca_context_max = max(native_pca_context_max,
                        max(abs(float(evaluated_pca[bone][r][c]) - float(evaluated_native[bone][r][c]))
                            for r in range(4) for c in range(4)))
                for bone in native_poses:
                    if bone not in profile_bones:
                        unmodeled_native_pca_max = max(unmodeled_native_pca_max,
                            max(abs(float(evaluated_pca[bone][r][c]) - float(evaluated_native[bone][r][c]))
                                for r in range(4) for c in range(4)))
                dataset_sample = dataset_times.get(float(time))
                if dataset_sample:
                    for bone in profile_bones:
                        error = max(abs(float(native_poses[bone][r][c]) -
                                float(dataset_sample["bones"][bone]["armature_pose"][r][c]))
                            for r in range(4) for c in range(4))
                        if error > dataset_native_context_max:
                            dataset_native_context_max = error
                            dataset_native_context_worst = {"bone": bone, "time": time,
                                "max_matrix_error": error}

                native_vertices = evaluated_mesh_vertices(native_meshes)
                pca_vertices = evaluated_mesh_vertices(pca_meshes)
                frame_error = mesh_overlay_error(native_vertices, pca_vertices)
                overlay_max = max(overlay_max, frame_error["error_world"])
                overlay_sq += frame_error["rmse_world"] ** 2 * frame_error["vertex_count"]
                overlay_count += frame_error["vertex_count"]
                if frame_error["error_world"] >= worst_overlay["error_world"]:
                    worst_overlay = {**frame_error, "time": time}
                per_sample.append({"time": time, "mesh_vertex_error_world": frame_error["error_world"],
                    "mesh_vertex_rmse_world": frame_error["rmse_world"]})
                native_samples.append((time, native_basis))
                pca_samples.append((time, pca_basis))

            native_action = make_action(native_rig, "NativeReference_" + name, native_samples)
            pca_action = make_action(pca_rig, "PCAOverlay_" + name, pca_samples)
            preview_actions.extend([native_action, pca_action])
            native_rig.matrix_world = target_original.matrix_world.copy()
            pca_rig.matrix_world = target_original.matrix_world.copy()
            bpy.context.view_layer.update()
            # Only the two overlay collections are visible when opened. Toggle
            # NativeReference in the Outliner to compare it with PCAOverlay.
            for obj in original_objects:
                obj.hide_set(True)
            for obj in preview_objects:
                obj.hide_set(False)
            native_collection.hide_viewport = True
            pca_collection.hide_viewport = False
            bpy.context.view_layer.objects.active = pca_rig
            pca_rig.select_set(True)

            output_path = os.path.abspath(os.path.join(args.preview_dir, name + ".blend"))
            bpy.ops.wm.save_as_mainfile(filepath=output_path)
            report_actions.append({"action": name, "file": output_path,
                "native_collection": native_collection.name, "pca_collection": pca_collection.name,
                "native_rig": native_rig.name, "pca_rig": pca_rig.name,
                "native_root": native_root.name, "pca_root": pca_root.name,
                "native_action": native_action.name, "pca_action": pca_action.name,
                "native_mesh_objects": [obj.name for obj in native_meshes],
                "pca_mesh_objects": [obj.name for obj in pca_meshes],
                "source_mesh_objects": [obj.name for obj in mesh_sources],
                "source_action_key_sample_count": len(source_times),
                "native_root_world_matrix": [[float(native_root.matrix_world[r][c]) for c in range(4)] for r in range(4)],
                "pca_root_world_matrix": [[float(pca_root.matrix_world[r][c]) for c in range(4)] for r in range(4)],
                "max_native_basis_matrix_error": native_basis_max,
                "max_pca_context_matrix_error": context_basis_max,
                "max_native_vs_pca_context_matrix_error": native_pca_context_max,
                "max_unmodeled_native_vs_pca_matrix_error": unmodeled_native_pca_max,
                "max_native_vs_dataset_context_matrix_error": dataset_native_context_max,
                "worst_native_vs_dataset_context_matrix_error": dataset_native_context_worst,
                "max_full_character_overlay_vertex_error_world": overlay_max,
                "rmse_full_character_overlay_vertex_error_world": (overlay_sq / max(1, overlay_count)) ** 0.5,
                "worst_overlay_vertex": worst_overlay, "per_sample": per_sample})
            clean_preview_ids(preview_objects, preview_actions, [native_collection, pca_collection])
            for obj in original_objects:
                obj.hide_set(original_visibility[obj])

        report = {"schema": "mg1.native-arm-context-pca-full-character-overlay.v1",
            "holdout_actions": [row["action"] for row in report_actions],
            "actions": report_actions,
            "max_full_character_overlay_vertex_error_world": max(
                row["max_full_character_overlay_vertex_error_world"] for row in report_actions),
            "max_native_basis_matrix_error": max(row["max_native_basis_matrix_error"] for row in report_actions),
            "max_pca_context_matrix_error": max(row["max_pca_context_matrix_error"] for row in report_actions),
            "max_native_vs_pca_context_matrix_error": max(row["max_native_vs_pca_context_matrix_error"] for row in report_actions),
            "max_unmodeled_native_vs_pca_matrix_error": max(row["max_unmodeled_native_vs_pca_matrix_error"] for row in report_actions),
            "max_native_vs_dataset_context_matrix_error": max(row["max_native_vs_dataset_context_matrix_error"] for row in report_actions),
            "overlay_gate": "pass" if max(row["max_full_character_overlay_vertex_error_world"]
                for row in report_actions) <= args.max_overlay_error else "fail",
            "overlay_error_tolerance_world": args.max_overlay_error,
            "preview_policy": "one standalone file per holdout; native and PCA root empties preserve the exact source object transform and intentional scale; non-profile bones retain full native converted transforms; all rigged meshes and parented accessories are duplicated",
            "model": {"type": "training-only PCA orthogonal projection",
                "training_action_count": len(dataset["training_actions"]),
                "holdout_action_count": len(dataset["holdout_actions"]),
                "latent_dimensions": args.latent_dimensions,
                "source_schema": dataset["source_schema"],
                "input": "full native holdout context transforms at exact action keys/subframes; only projected context bones replace native pose"},
            "production_mutated": False}
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print("MG1_CONTEXT_PCA_FULL_OVERLAYS=" + json.dumps({
            "files": [row["file"] for row in report_actions],
            "max_full_character_overlay_vertex_error_world": report["max_full_character_overlay_vertex_error_world"],
            "max_native_basis_matrix_error": report["max_native_basis_matrix_error"],
            "max_pca_context_matrix_error": report["max_pca_context_matrix_error"]}))
        if report["overlay_gate"] != "pass":
            raise RuntimeError("full-character PCA overlays exceed configured tolerance")
    finally:
        bpy.context.scene.frame_set(0)
        for obj in original_objects:
            if obj.name in bpy.data.objects:
                obj.hide_set(original_visibility[obj])
        for obj in list(bpy.data.objects):
            if obj not in original_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for action in list(bpy.data.actions):
            if action not in original_actions:
                bpy.data.actions.remove(action, do_unlink=True)
        for collection in list(bpy.data.collections):
            if collection not in original_collections:
                bpy.data.collections.remove(collection)


if __name__ == "__main__":
    main()
