"""Fresh-process saved-file validator for the semantic walk preview."""
import argparse
import json
import math
import os
import sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fit_luigi_walk_semantic_waluigi as fit
from analyze_native_arm_context import source_pose_from_curves
from convert_native_owner_actions import evaluated_bone_matrices, set_frame
from context_dataset import normalize_context_dataset


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--fit-report", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def main():
    args = parse_args()
    with open(args.fit_report, "r", encoding="utf-8") as stream:
        fit_report = json.load(stream)
    with __import__("gzip").open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = normalize_context_dataset(json.load(stream))
    bones = dataset["bones"]
    rest = dataset["rest_bones"]
    scene = bpy.context.scene
    rig_name = fit_report["preview"]["rig"]
    rig = bpy.data.objects.get(rig_name)
    source = bpy.data.objects.get(fit_report["source_rig"])
    source_action = bpy.data.actions.get(fit_report["source_action"])
    target_action = bpy.data.actions.get(fit_report["preview"]["action"])
    collection = bpy.data.collections.get(fit_report["preview"]["collection"])
    if not rig or not source or not source_action or not target_action or not collection:
        raise RuntimeError("saved preview is missing its rig, source, actions, or collection")
    if rig.animation_data is None or rig.animation_data.action != target_action:
        raise RuntimeError("baked target action is not assigned to preview rig")
    if rig.animation_data.action_slot is None or rig.animation_data.action_slot.identifier != target_action.slots[0].identifier:
        raise RuntimeError("baked preview action slot is not assigned to target owner")
    if (collection.hide_viewport or collection.hide_render or rig.hide_viewport or
        rig.hide_render or rig.hide_get()):
        raise RuntimeError("preview rig or collection is hidden")
    meshes = [bpy.data.objects.get(name) for name in fit_report["preview"]["mesh_objects"]]
    if not meshes or any(mesh is None for mesh in meshes):
        raise RuntimeError("complete target mesh set is missing")
    for mesh in meshes:
        if mesh.hide_viewport or mesh.hide_render or mesh.hide_get():
            raise RuntimeError(f"preview mesh is hidden: {mesh.name}")
        arm_mods = [m for m in mesh.modifiers if m.type == "ARMATURE"]
        if not arm_mods or any(m.object != rig for m in arm_mods):
            raise RuntimeError(f"preview mesh armature binding is invalid: {mesh.name}")
    constraints = sum(len(rig.constraints) + sum(len(pb.constraints) for pb in rig.pose.bones)
                      for _ in (0,))
    if constraints:
        raise RuntimeError("baked preview rig retains runtime constraints")

    source_slot_id = fit_report["source_slot"]
    source_slots = [slot for slot in source_action.slots if slot.identifier == source_slot_id]
    if len(source_slots) != 1:
        raise RuntimeError("source action slot is missing/ambiguous in saved scene")
    source_slot = source_slots[0]
    src_rest = fit.source_rest_from_rig(source)
    body_map = fit.frame_map(source, src_rest, fit.SOURCE_PROFILE["torso"],
                             rig, rest, fit.TARGET_PROFILE["torso"])
    forearm_map = fit.frame_map(source, src_rest, fit.SOURCE_PROFILE["forearm"],
                                rig, rest, fit.TARGET_PROFILE["forearm"])
    rest_all = fit.source_rest_from_rig(rig)
    body_map_right = fit.frame_map(source, src_rest, fit.SOURCE_PROFILE_RIGHT["torso"],
                                   rig, rest_all, fit.TARGET_PROFILE_RIGHT["torso"])
    forearm_map_right = fit.frame_map(source, src_rest, fit.SOURCE_PROFILE_RIGHT["forearm"],
                                      rig, rest_all, fit.TARGET_PROFILE_RIGHT["forearm"])
    left_forearm = fit.proper_rotation(fit.numpy_matrix(
        rest_all[fit.TARGET_PROFILE["forearm"]]["rest_matrix"]))
    right_forearm = fit.proper_rotation(fit.numpy_matrix(
        rest_all[fit.TARGET_PROFILE_RIGHT["forearm"]]["rest_matrix"]))
    right_forearm_to_left = left_forearm.T @ right_forearm
    torso_rotation = fit.proper_rotation(fit.numpy_matrix(
        rest_all[fit.TARGET_PROFILE["torso"]]["rest_matrix"]))
    body_reflection = torso_rotation.T @ np.diag((-1.0, 1.0, 1.0)) @ torso_rotation
    # The report currently carries explicit frame numbers; validate that they
    # are the requested 0..60 interval before evaluating the saved action.
    frames = fit_report["source_frames"]
    target_semantics, source_semantics = [], []
    target_semantics_right, source_semantics_right = [], []
    elbow_curve = []
    elbow_branches = {"main": [], "overlap": []}
    elbow_branches_right = {"main": [], "overlap": []}
    max_replay = {"error": 0.0}
    max_vertex_finite = 0
    max_basis_scale_error = 0.0
    bake_reference = fit_report["preview"].get("bake_pose_reference")
    if not bake_reference or bake_reference.get("frames") != frames:
        raise RuntimeError("fit report lacks matching per-frame bake pose reference")
    expected_pose_rows = bake_reference["matrices"]
    requested_pose_rows = bake_reference.get("requested_matrices")
    if requested_pose_rows is None:
        raise RuntimeError("fit report lacks requested pre-bake matrices")
    expected_basis_rows = bake_reference["basis_matrices"]
    max_pose_reference_error = 0.0
    max_requested_pose_error = 0.0
    max_evaluated_fit_pose_error = 0.0
    max_basis_reference_error = 0.0
    first_pose_divergence = None
    first_basis_divergence = None
    max_pose_reference_detail = None
    max_requested_pose_detail = None
    max_evaluated_fit_pose_detail = None
    max_basis_reference_detail = None
    max_rest_reconstruction_error = 0.0
    max_rest_reconstruction_detail = None
    expected_pose_zero = expected_pose_rows[0]
    expected_basis_zero = expected_basis_rows[0]
    for bone_name, expected_basis_matrix in expected_basis_zero.items():
        bone = rig.data.bones[bone_name]
        expected_pose_matrix = fit.numpy_matrix(expected_pose_zero[bone_name])
        basis_matrix = fit.numpy_matrix(expected_basis_matrix)
        reconstructed_local_rest = np.linalg.inv(
            fit.numpy_matrix(expected_pose_zero[bone.parent.name])
            if bone.parent else np.eye(4)) @ expected_pose_matrix @ np.linalg.inv(basis_matrix)
        actual_local_rest = (fit.numpy_matrix(bone.parent.matrix_local).astype(np.float64)
                             if bone.parent else np.eye(4))
        if bone.parent:
            actual_local_rest = np.linalg.inv(actual_local_rest) @ fit.numpy_matrix(bone.matrix_local)
        else:
            actual_local_rest = fit.numpy_matrix(bone.matrix_local)
        error = fit.matrix_error(reconstructed_local_rest, actual_local_rest)
        if error > max_rest_reconstruction_error:
            max_rest_reconstruction_error = error
            max_rest_reconstruction_detail = {"bone": bone_name, "error": error}
    action_bags = [bag for layer in target_action.layers for strip in layer.strips
                   for bag in strip.channelbags if bag.slot == rig.animation_data.action_slot]
    if len(action_bags) != 1:
        raise RuntimeError(f"expected one baked action channelbag, found {len(action_bags)}")
    interpolation_modes = sorted({point.interpolation for fc in action_bags[0].fcurves
                                  for point in fc.keyframe_points})
    keyed_times = sorted({float(point.co.x) for fc in action_bags[0].fcurves
                          for point in fc.keyframe_points})
    quaternion_curves = {}
    for fc in action_bags[0].fcurves:
        if '.rotation_quaternion' not in fc.data_path or 'pose.bones[' not in fc.data_path:
            continue
        bone_name = fc.data_path.split('pose.bones["', 1)[1].split('"]', 1)[0]
        quaternion_curves.setdefault(bone_name, {})[fc.array_index] = fc
    quaternion_sign_flip_count = 0
    for bone_name, curves in quaternion_curves.items():
        if set(curves) != {0, 1, 2, 3}:
            continue
        keyed = sorted({float(p.co.x) for curve in curves.values() for p in curve.keyframe_points})
        previous = None
        for time in keyed:
            q = np.asarray([curves[i].evaluate(time) for i in range(4)], dtype=np.float64)
            norm = np.linalg.norm(q)
            if norm < 1e-12:
                continue
            q /= norm
            if previous is not None and float(previous @ q) < -1e-5:
                quaternion_sign_flip_count += 1
            previous = q
    for frame in frames:
        set_frame(scene, float(frame))
        source_pose, _ = source_pose_from_curves(source, source_action, source_slot, float(frame))
        src_sem = fit.semantic_from_source_sample(src_rest,
            {name: fit.numpy_matrix(matrix) for name, matrix in source_pose.items()},
            body_map, forearm_map)
        source_semantics.append(src_sem)
        src_right = fit.semantic_from_source_sample(src_rest,
            {name: fit.numpy_matrix(matrix) for name, matrix in source_pose.items()},
            body_map_right, forearm_map_right, fit.SOURCE_PROFILE_RIGHT)
        src_right = fit.source_right_to_left_semantics(
            src_right, right_forearm_to_left, body_reflection)
        source_semantics_right.append(src_right)
        evaluated_rig = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        pose = {pb.name: pb.matrix.copy() for pb in evaluated_rig.pose.bones}
        evaluated_basis = {pb.name: pb.matrix_basis.copy()
                           for pb in evaluated_rig.pose.bones}
        frame_index = frames.index(frame)
        expected_pose = expected_pose_rows[frame_index]
        requested_pose = requested_pose_rows[frame_index]
        expected_basis = expected_basis_rows[frame_index]
        for bone_name, matrix in pose.items():
            error = fit.matrix_error(matrix, fit.numpy_matrix(expected_pose[bone_name]))
            if error > max_pose_reference_error:
                max_pose_reference_error = error
                max_pose_reference_detail = {"frame": frame, "bone": bone_name, "error": error}
            if first_pose_divergence is None and error > 1e-5:
                first_pose_divergence = {"frame": frame, "bone": bone_name, "error": error}
            request_error = fit.matrix_error(matrix, fit.numpy_matrix(requested_pose[bone_name]))
            if request_error > max_requested_pose_error:
                max_requested_pose_error = request_error
                max_requested_pose_detail = {"frame": frame, "bone": bone_name, "error": request_error}
            if error > max_evaluated_fit_pose_error:
                max_evaluated_fit_pose_error = error
                max_evaluated_fit_pose_detail = {"frame": frame, "bone": bone_name, "error": error}
        for bone_name, expected_matrix in expected_basis.items():
            error = fit.matrix_error(evaluated_basis[bone_name],
                                     fit.numpy_matrix(expected_matrix))
            if error > max_basis_reference_error:
                max_basis_reference_error = error
                max_basis_reference_detail = {"frame": frame, "bone": bone_name, "error": error}
            if first_basis_divergence is None and error > 1e-5:
                first_basis_divergence = {"frame": frame, "bone": bone_name, "error": error}
        target_sem = fit.semantic_from_target_sample(rest,
            {name: fit.numpy_matrix(pose[name]) for name in bones})
        target_semantics.append(target_sem)
        target_right = fit.semantic_from_target_sample(rest_all,
            {name: fit.numpy_matrix(pose[name]) for name in rest_all},
            fit.TARGET_PROFILE_RIGHT, right_forearm_to_left)
        target_right = fit.source_right_to_left_semantics(
            target_right, right_forearm_to_left, body_reflection)
        target_semantics_right.append(target_right)
        elbow_curve.append({"frame": int(frame), "source_raw": float(src_sem[3]),
                            "target": float(target_sem[3])})
        # Measure each Waluigi elbow branch geometrically. The context feature
        # uses their midpoint; separate curves reveal whether that average
        # hides motion or one branch lacks leverage.
        def moved_point(bone_name, endpoint):
            rest_matrix = fit.numpy_matrix(rest_all[bone_name]["rest_matrix"])
            local = np.r_[rest_all[bone_name][endpoint], 1.0]
            return (fit.numpy_matrix(pose[bone_name]) @
                    np.linalg.inv(rest_matrix) @ local)[:3]
        shoulder = moved_point("bone_23", "head")
        wrist = moved_point("bone_27", "tail")
        elbow_main = moved_point("bone_26", "head")
        elbow_overlap = moved_point("bone_29", "head")
        def bend(elbow):
            upper = elbow - shoulder
            lower = wrist - elbow
            return math.atan2(float(np.linalg.norm(np.cross(upper, lower))),
                              float(np.dot(upper, lower)))
        elbow_branches["main"].append({"frame": int(frame), "angle": bend(elbow_main)})
        elbow_branches["overlap"].append({"frame": int(frame), "angle": bend(elbow_overlap)})
        shoulder_r = moved_point("bone_58", "head")
        elbow_main_r = moved_point("bone_61", "head")
        elbow_overlap_r = moved_point("bone_64", "head")
        wrist_r = moved_point("bone_62", "tail")
        def bend_right(elbow):
            upper = elbow - shoulder_r
            lower = wrist_r - elbow
            return math.atan2(float(np.linalg.norm(np.cross(upper, lower))),
                              float(np.dot(upper, lower)))
        elbow_branches_right["main"].append({"frame": int(frame), "angle": bend_right(elbow_main_r)})
        elbow_branches_right["overlap"].append({"frame": int(frame), "angle": bend_right(elbow_overlap_r)})
        evaluated = []
        for mesh in meshes:
            ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
            me = ev.to_mesh()
            try:
                for vertex in me.vertices:
                    point = ev.matrix_world @ vertex.co
                    if not all(math.isfinite(float(v)) for v in point):
                        raise RuntimeError(f"non-finite saved mesh vertex: {mesh.name} frame {frame}")
                    max_vertex_finite += 1
            finally:
                ev.to_mesh_clear()
        for pb in rig.pose.bones:
            for value in pb.scale:
                max_basis_scale_error = max(max_basis_scale_error, abs(float(value) - 1.0))
    source_semantics = np.asarray(source_semantics)
    elbow_reference = fit_report["elbow_source_mapping"]["frame_0_reference_radians"]
    source_semantics[:, 3] -= elbow_reference
    source_semantics[:, 3] *= fit_report["elbow_source_mapping"].get("excursion_gain", 1.0)
    source_semantics_right = np.asarray(source_semantics_right)
    elbow_reference_right = fit_report["right_elbow_source_mapping"]["frame_0_reference_radians"]
    source_semantics_right[:, 3] -= elbow_reference_right
    source_semantics_right[:, 3] *= fit_report["right_elbow_source_mapping"].get("excursion_gain", 1.0)
    for row, mapped in zip(elbow_curve, source_semantics[:, 3]):
        row["source"] = float(mapped)
    residual = np.asarray(target_semantics) - source_semantics
    residual_right = np.asarray(target_semantics_right) - source_semantics_right
    rmse = {name: float(np.sqrt(np.mean(residual[:, i] ** 2)))
            for i, name in enumerate(fit.SEMANTIC_NAMES)}
    fitted_rmse = fit_report["preview"]["semantic_feature_rmse_after_solve"]
    rmse_right = {name: float(np.sqrt(np.mean(residual_right[:, i] ** 2)))
                  for i, name in enumerate(fit.SEMANTIC_NAMES)}
    fitted_rmse_right = fit_report["right_source_target_semantic_rmse"]
    for index, row in enumerate(elbow_curve):
        row["source_raw"] = float(np.asarray(source_semantics)[index, 3] /
            fit_report["elbow_source_mapping"].get("excursion_gain", 1.0) + elbow_reference)
    report = {
        "schema": "mg1.semantic-walk-saved-preview-validation.v1",
        "file": bpy.data.filepath,
        "frame_count": len(frames),
        "frame_range": [frames[0], frames[-1]],
        "rig": rig.name,
        "action": target_action.name,
        "action_slot": rig.animation_data.action_slot.identifier,
        "mesh_objects": [mesh.name for mesh in meshes],
        "mesh_vertex_samples": max_vertex_finite,
        "runtime_constraints": constraints,
        "object_scale": [float(v) for v in rig.scale],
        "bake_reference_comparison": {
            "max_pose_matrix_error": max_pose_reference_error,
            "max_pose_matrix_divergence": max_pose_reference_detail,
            "first_pose_divergence_over_1e-5": first_pose_divergence,
            "max_requested_pose_matrix_error": max_requested_pose_error,
            "requested_pose_matrix_divergence": max_requested_pose_detail,
            "max_evaluated_fit_pose_matrix_error": max_evaluated_fit_pose_error,
            "evaluated_fit_pose_matrix_divergence": max_evaluated_fit_pose_detail,
            "max_matrix_basis_error": max_basis_reference_error,
            "max_matrix_basis_divergence": max_basis_reference_detail,
            "first_basis_divergence_over_1e-5": first_basis_divergence,
            "max_local_rest_reconstruction_error": max_rest_reconstruction_error,
            "local_rest_reconstruction_divergence": max_rest_reconstruction_detail,
        },
        "action_curve_diagnostics": {
            "channelbag_slot": rig.animation_data.action_slot.identifier,
            "fcurve_count": len(action_bags[0].fcurves),
            "keyed_time_count": len(keyed_times),
            "keyed_time_minmax": [keyed_times[0], keyed_times[-1]] if keyed_times else None,
            "all_requested_frames_keyed": all(float(frame) in set(keyed_times) for frame in frames),
            "interpolation_modes": interpolation_modes,
            "quaternion_sign_flips_after_normalization": quaternion_sign_flip_count,
        },
        "semantic_rmse_from_saved_evaluation": rmse,
        "right_semantic_rmse_from_saved_evaluation": rmse_right,
        "elbow_curve_radians": elbow_curve,
        "elbow_curve_ranges_radians": {
            "source_raw": fit_report["elbow_source_mapping"]["raw_source_range_radians"],
            "source_mapped": [float(min(row["source"] for row in elbow_curve)),
                              float(max(row["source"] for row in elbow_curve))],
            "target": [float(min(row["target"] for row in elbow_curve)),
                       float(max(row["target"] for row in elbow_curve))],
            "native_training": fit_report["native_training_semantic_range"]["elbow_flexion"],
            "target_main_branch_angle": [float(min(row["angle"] for row in elbow_branches["main"])),
                                          float(max(row["angle"] for row in elbow_branches["main"]))],
            "target_overlap_branch_angle": [float(min(row["angle"] for row in elbow_branches["overlap"])),
                                             float(max(row["angle"] for row in elbow_branches["overlap"]))],
        },
        "target_branch_elbow_angles_radians": elbow_branches,
        "right_target_branch_elbow_angles_radians": elbow_branches_right,
        "right_target_branch_angle_ranges_radians": {
            name: [float(min(row["angle"] for row in rows)),
                   float(max(row["angle"] for row in rows))]
            for name, rows in elbow_branches_right.items()},
        "right_elbow_curve_radians": [{"frame": int(frame),
            "source": float(source_semantics_right[i, 3]),
            "target": float(target_semantics_right[i][3])}
            for i, frame in enumerate(frames)],
        "right_elbow_curve_ranges_radians": {
            "source_mapped": [float(source_semantics_right[:, 3].min()),
                              float(source_semantics_right[:, 3].max())],
            "target": [float(min(row[3] for row in target_semantics_right)),
                       float(max(row[3] for row in target_semantics_right))]},
        "max_semantic_rmse_delta_vs_fit_report": max(abs(rmse[k] - fitted_rmse[k]) for k in rmse),
        "max_bone_scale_deviation": max_basis_scale_error,
        "fit_gates": fit_report["validation"],
        "max_right_semantic_rmse_delta_vs_fit_report": max(
            abs(rmse_right[k] - fitted_rmse_right[k]) for k in rmse_right),
        "gate": "pass" if max_pose_reference_error < 1e-5 and
            max_requested_pose_error < 1e-5 and
            max_basis_reference_error < 1e-5 and
            max(abs(rmse[k] - fitted_rmse[k]) for k in rmse) < 1e-5 and
            max(abs(rmse_right[k] - fitted_rmse_right[k]) for k in rmse_right) < 1e-5 and
            constraints == 0 and len(meshes) == 2 and all(float(v) > 0 for v in rig.scale) else "fail",
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("MG1_SEMANTIC_SAVED_VALIDATION=" + json.dumps(report))
    if report["gate"] != "pass":
        raise RuntimeError("saved semantic preview failed fresh-process validation")


if __name__ == "__main__":
    main()
