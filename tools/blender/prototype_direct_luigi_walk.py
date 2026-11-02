"""Disposable direct Galaxy-action transfer probe for Blender 5.2+.

Example:
  blender --background approved_walk_scene.blend --python \
    tools/blender/prototype_direct_luigi_walk.py -- \
    --report tools/blender/reports/direct_luigi_walk_probe.json \
    --preview tools/blender/reports/direct_luigi_walk_candidate.blend

The input .blend is never saved. The script creates a new target action and
preview only if its numerical gates pass. It samples the selected source
action slot's original FCurves onto a disposable control-rig duplicate.
"""
import argparse
import json
import math
import os
import re
import sys

import bpy
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import duplicate_rig
from direct_galaxy_adapter import rest_calibrated_rotation, validate_mapping
from test_semantic_preview_noop import copy_action_slot, duplicate_mesh


LEFT = {
    "ShoulderL": ("bone_22", "bone_23"),
    "ArmL1": ("bone_25", "bone_26"),
    "ArmL2": ("bone_27", "bone_29"),
    "HandL": ("bone_30",),
}
RIGHT = {
    "ShoulderR": ("bone_57", "bone_58"),
    "ArmR1": ("bone_60", "bone_61"),
    "ArmR2": ("bone_62", "bone_64"),
    "HandR": ("bone_65",),
}
ARM_OUTPUTS = tuple(dict.fromkeys(t for m in (LEFT, RIGHT)
                                  for targets in m.values() for t in targets))


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", default="luigi.001_armature_walk")
    p.add_argument("--target-rig", default="Waluigi_Arm_Test")
    p.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    p.add_argument("--source-mesh-prefix", default="ARMTEST_")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=60)
    p.add_argument("--report", required=True)
    p.add_argument("--preview", required=True)
    p.add_argument("--max-jump-radians", type=float, default=0.5)
    p.add_argument("--max-glove-gap-delta", type=float, default=0.02)
    return p.parse_args(argv)


def rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def mat_err(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def object_basis(obj):
    # The validated saved workfile carries the intentional 43.437 transform
    # in matrix_basis while matrix_world can reopen as identity.
    return obj.matrix_basis.copy()


def action_slot(action, wanted=None):
    slots = [s for s in action.slots if wanted is None or s.identifier == wanted]
    if len(slots) != 1:
        raise RuntimeError(f"{action.name}: expected one action slot, found {len(slots)}")
    return slots[0]


def quat_angle(a, b):
    qa = a.copy() if hasattr(a, "w") else a.to_quaternion()
    qb = b.copy() if hasattr(b, "w") else b.to_quaternion()
    qa.normalize(); qb.normalize()
    return 2.0 * math.acos(max(-1.0, min(1.0, abs(float(qa.dot(qb))))) )


def matrix_joint_gap(rig, pose_matrices, parent_name, child_name):
    parent_bone = rig.data.bones[parent_name]
    parent_tail = pose_matrices[parent_name] @ Matrix.Translation(
        (0.0, parent_bone.length, 0.0)).translation
    child_head = pose_matrices[child_name].translation
    return (parent_tail - child_head).length


def fcurves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    yield curve


def curves_for_slot(action, slot):
    bags = [bag for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags if bag.slot == slot]
    if len(bags) != 1:
        raise RuntimeError(f"{action.name}: expected one channelbag for {slot.identifier}")
    return list(bags[0].fcurves)


SOURCE_BONE_PATH = re.compile(
    r'^pose\.bones\["([^"]+)"\]\.(location|rotation_euler|rotation_quaternion|scale)$')


def evaluate_source_curves(control, curves, frame):
    """Apply the source action's original layered FCurves at one sample time.

    The saved scene's slot reports the right action but its active action layer
    evaluates only frame-zero bone channels in background mode. Sampling the
    selected slot's FCurves directly avoids treating that stale owner binding
    as authoritative while preserving original curve timing/interpolation.
    """
    for pb in control.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    values = {}
    for curve in curves:
        match = SOURCE_BONE_PATH.match(curve.data_path)
        if not match:
            continue
        bone_name, prop = match.groups()
        values.setdefault((bone_name, prop), [None] * (4 if prop == 'rotation_quaternion' else 3))[
            curve.array_index] = float(curve.evaluate(frame))
    for (bone_name, prop), components in values.items():
        pb = control.pose.bones.get(bone_name)
        if pb is None:
            raise RuntimeError(f"source action references missing bone {bone_name}")
        current = list(getattr(pb, prop))
        for index, value in enumerate(components):
            if value is not None:
                current[index] = value
        setattr(pb, prop, current)
    bpy.context.view_layer.update()


def pose_matrices_from_basis(rig):
    """Rebuild armature-space pose matrices from rest and local basis channels."""
    result = {}
    pending = set(rig.data.bones.keys())
    while pending:
        progressed = False
        for name in tuple(pending):
            bone = rig.data.bones[name]
            parent = bone.parent
            if parent and parent.name in pending:
                continue
            rest = bone.matrix_local.copy()
            basis = rig.pose.bones[name].matrix_basis.copy()
            if parent:
                rest_local = parent.matrix_local.inverted_safe() @ rest
                result[name] = result[parent.name] @ rest_local @ basis
            else:
                result[name] = rest @ basis
            pending.remove(name)
            progressed = True
        if not progressed:
            raise RuntimeError("armature hierarchy cannot be traversed")
    return result


def set_pose_rotation_from_armature_matrix(rig, name, desired, pose_matrices):
    """Set one bone's armature-space orientation while retaining its location."""
    bone = rig.data.bones[name]
    parent = bone.parent
    if parent:
        parent_pose = pose_matrices[parent.name]
        rest_local = parent.matrix_local.inverted_safe() @ bone.matrix_local
        local_pose = parent_pose.inverted_safe() @ desired
        basis = rest_local.inverted_safe() @ local_pose
    else:
        basis = bone.matrix_local.inverted_safe() @ desired
    rig.pose.bones[name].matrix_basis = basis


def branch_relative_excursion(pose_by_frame, bone_a, bone_b):
    relatives = []
    for frame in sorted(pose_by_frame):
        qa = pose_by_frame[frame][bone_a].to_quaternion()
        qb = pose_by_frame[frame][bone_b].to_quaternion()
        relatives.append(qa.inverted() @ qb)
    return max((quat_angle(relatives[0], q) for q in relatives[1:]), default=0.0)


def hide_reference_objects(visible_set):
    for obj in bpy.data.objects:
        show = obj in visible_set
        obj.hide_render = not show
        obj.hide_set(not show)


def main():
    args = parse_args()
    scene = bpy.context.scene
    source = bpy.data.objects.get(args.source_rig)
    target_base = bpy.data.objects.get(args.target_rig)
    source_action = bpy.data.actions.get(args.source_action)
    walk_action = bpy.data.actions.get(args.walk_action)
    if not all((source, target_base, source_action, walk_action)):
        raise RuntimeError("source/action/target/baseline action not found")
    if source.type != 'ARMATURE' or target_base.type != 'ARMATURE':
        raise RuntimeError("source and target must be armatures")
    if len(target_base.data.bones) != 92:
        raise RuntimeError("target must be the expected 92-bone Waluigi rig")
    if abs(float(target_base.matrix_basis.to_scale().x) - 43.437) > 0.01:
        raise RuntimeError("Waluigi authored object scale changed or is unavailable")
    source_slot = action_slot(source_action,
                              source.animation_data.action_slot.identifier
                              if source.animation_data and source.animation_data.action_slot else None)
    walk_slot = action_slot(walk_action,
                            target_base.animation_data.action_slot.identifier
                            if target_base.animation_data and target_base.animation_data.action_slot else None)
    validate_mapping(LEFT, source.data.bones.keys(), target_base.data.bones.keys())
    validate_mapping(RIGHT, source.data.bones.keys(), target_base.data.bones.keys())
    mesh_sources = [o for o in bpy.data.objects
                    if o.type == 'MESH' and o.name.startswith(args.source_mesh_prefix)
                    and any(m.type == 'ARMATURE' and m.object == target_base
                            for m in o.modifiers)]
    if not mesh_sources:
        raise RuntimeError("no explicitly prefixed meshes bound to baseline target")

    frames = list(range(args.start, args.end + 1))
    if frames != list(range(0, 61)):
        raise RuntimeError("prototype is intentionally restricted to frames 0-60")
    original_frame = scene.frame_current
    original_subframe = scene.frame_subframe

    # Duplicate source rest data, then evaluate the action's original curves
    # directly. In this saved workfile, the assigned layered action slot reports
    # the correct owner but leaves its pose at frame zero in background mode.
    control = duplicate_rig(source, "DirectWalk_Luigi_Control")
    control.matrix_basis = object_basis(source)
    # The Galaxy walk stores rotation_euler curves. duplicate_rig() deliberately
    # normalizes disposable rigs to quaternion mode, so restore the source
    # channel modes before binding its original layered action.
    for name in source.pose.bones.keys():
        original_pb = source.pose.bones[name]
        control_pb = control.pose.bones[name]
        control_pb.rotation_mode = original_pb.rotation_mode
        control_pb.location = (0.0, 0.0, 0.0)
        control_pb.rotation_euler = (0.0, 0.0, 0.0)
        control_pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        control_pb.scale = (1.0, 1.0, 1.0)
    control.animation_data_clear()
    source_curves = curves_for_slot(source_action, source_slot)

    # Duplicate Waluigi's validated clean-basis walk rig and bound meshes.
    target = duplicate_rig(target_base, "DirectWalk_Waluigi_Target")
    target.matrix_basis = object_basis(target_base)
    target.data.name = target.name + "_Data"
    target_action = copy_action_slot(walk_action, walk_slot, target,
                                     "Waluigi_DirectGalaxyWalk_ArmAdapter_v1")
    target_baseline_curves = curves_for_slot(target_action, target.animation_data.action_slot)
    collection = bpy.data.collections.new("DirectGalaxyWalk_Candidate")
    scene.collection.children.link(collection)
    for obj in (control, target):
        for col in list(obj.users_collection):
            col.objects.unlink(obj)
        collection.objects.link(obj)
    target_meshes = [duplicate_mesh(o, target, collection, "DirectWalk_")
                     for o in mesh_sources]
    target_mesh_names = [o.name for o in target_meshes]

    srot = object_basis(control).to_3x3().to_quaternion().to_matrix()
    trot = object_basis(target).to_3x3().to_quaternion().to_matrix()
    desired_by_frame = {}
    jumps = {name: 0.0 for name in ARM_OUTPUTS}
    previous = {}
    semantic_bones = ("ShoulderL", "ArmL1", "ArmL2", "HandL",
                      "ShoulderR", "ArmR1", "ArmR2", "HandR")
    source_rest_deltas = {key: [] for key in semantic_bones}
    source_channel_samples = {}
    glove_gaps = {"left": [], "right": []}
    baseline_fingers = {}
    baseline_other_errors = 0.0
    worst_step = {"radians": 0.0, "frame": None, "bone": None}

    try:
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            evaluate_source_curves(control, source_curves, frame)
            source_pose_matrices = pose_matrices_from_basis(control)
            if frame in (0, 15, 30, 45, 60):
                source_channel_samples[str(frame)] = {
                    n: {"mode": control.pose.bones[n].rotation_mode,
                        "euler": list(control.pose.bones[n].rotation_euler),
                        "basis": list(control.pose.bones[n].matrix_basis.to_quaternion()),
                        "evaluated": list(source_pose_matrices[n].to_quaternion())}
                    for n in semantic_bones}
            evaluate_source_curves(target, target_baseline_curves, frame)
            target_pose_matrices = pose_matrices_from_basis(target)
            pose_snapshot = {n: target.pose.bones[n].matrix_basis.copy()
                             for n in target.data.bones.keys()}
            for mapping in (LEFT, RIGHT):
                for source_name, target_names in mapping.items():
                    src_pose = source_pose_matrices[source_name]
                    src_rest = control.data.bones[source_name].matrix_local.copy()
                    for target_name in target_names:
                        rest = target.data.bones[target_name].matrix_local.copy()
                        rot = rest_calibrated_rotation(src_pose, src_rest,
                                                       srot, rest, trot)
                        # Start from the target's evaluated baseline pose. Only
                        # rotation is transferred; head position/length remain
                        # owned by the target's own hierarchy and walk action.
                        current = target_pose_matrices[target_name]
                        desired = Matrix.LocRotScale(current.translation,
                            rot.to_quaternion(), (1, 1, 1))
                        set_pose_rotation_from_armature_matrix(
                            target, target_name, desired, target_pose_matrices)
                        target_pose_matrices = pose_matrices_from_basis(target)
                        bpy.context.view_layer.update()
                        q = target_pose_matrices[target_name].to_quaternion()
                        if target_name in previous:
                            step = quat_angle(previous[target_name], q)
                            jumps[target_name] = max(jumps[target_name], step)
                            if step > worst_step["radians"]:
                                worst_step = {"radians": step, "frame": frame,
                                              "bone": target_name}
                        previous[target_name] = q.copy()
            bpy.context.view_layer.update()
            for name in ARM_OUTPUTS:
                pb = target.pose.bones[name]
                pb.rotation_mode = 'QUATERNION'
                pb.keyframe_insert(data_path="rotation_quaternion", frame=frame,
                                   group="Direct Galaxy Adapter")
                pb.keyframe_insert(data_path="location", frame=frame,
                                   group="Direct Galaxy Adapter")
                pb.keyframe_insert(data_path="scale", frame=frame,
                                   group="Direct Galaxy Adapter")
            desired_by_frame[frame] = {n: target_pose_matrices[n].copy()
                                       for n in ARM_OUTPUTS}
            for semantic_bone in semantic_bones:
                src_rest = control.data.bones[semantic_bone].matrix_local
                delta = source_pose_matrices[semantic_bone].to_3x3() @ src_rest.to_3x3().inverted()
                source_rest_deltas[semantic_bone].append(delta.to_quaternion())
            glove_gaps["left"].append(float(matrix_joint_gap(
                target, target_pose_matrices, "bone_27", "bone_30")))
            glove_gaps["right"].append(float(matrix_joint_gap(
                target, target_pose_matrices, "bone_62", "bone_65")))
            for hand in ("bone_31", "bone_32", "bone_33", "bone_34", "bone_35",
                         "bone_36", "bone_37", "bone_38", "bone_39", "bone_40",
                         "bone_41", "bone_42", "bone_43", "bone_44", "bone_45",
                         "bone_46", "bone_47", "bone_48", "bone_49", "bone_50",
                         "bone_51", "bone_66", "bone_67", "bone_68", "bone_69",
                         "bone_70", "bone_71", "bone_72", "bone_73", "bone_74",
                         "bone_75", "bone_76", "bone_77", "bone_78", "bone_79",
                         "bone_80", "bone_81", "bone_82", "bone_83", "bone_84",
                         "bone_85", "bone_86"):
                baseline_fingers[hand] = max(baseline_fingers.get(hand, 0.0),
                    mat_err(pose_snapshot[hand], target.pose.bones[hand].matrix_basis))
            # Non-arm channels must remain exactly on the approved baseline.
            for name, before in pose_snapshot.items():
                if name in ARM_OUTPUTS:
                    continue
                # Arm descendants inherit the arm pose; compare local basis,
                # which should remain baseline-identical on untouched channels.
                err = mat_err(before, target.pose.bones[name].matrix_basis)
                baseline_other_errors = max(baseline_other_errors, err)

        for curve in fcurves(target_action):
            is_new_arm_curve = any(f'pose.bones["{name}"]' in curve.data_path
                                   for name in ARM_OUTPUTS)
            if is_new_arm_curve:
                for point in curve.keyframe_points:
                    point.interpolation = 'LINEAR'
                curve.update()
        for pb in target.pose.bones:
            if len(pb.constraints):
                raise RuntimeError("baseline target unexpectedly contains runtime constraints")

        # Compare the exact baked/evaluated matrices and geometry after replay.
        bake_pose_error = 0.0
        max_step = 0.0
        prior_pose = {}
        for frame in frames:
            scene.frame_set(frame)
            evaluate_source_curves(target, list(fcurves(target_action)), frame)
            replay_pose_matrices = pose_matrices_from_basis(target)
            for name, intended in desired_by_frame[frame].items():
                actual = replay_pose_matrices[name]
                bake_pose_error = max(bake_pose_error, mat_err(actual, intended))
                if name in prior_pose:
                    max_step = max(max_step, quat_angle(prior_pose[name], actual))
                prior_pose[name] = actual.copy()
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
        max_glove_gap = max((max(v) for v in glove_gaps.values()), default=0.0)
        max_step = max(max_step, max(jumps.values(), default=0.0))
        finite = all(math.isfinite(v) for v in [bake_pose_error, max_step,
            max_glove_gap, baseline_other_errors] + list(jumps.values()))
        runtime_constraints = sum(len(pb.constraints) for pb in target.pose.bones)
        scale_error = max(abs(float(a) - float(b)) for a, b in
            zip(target.matrix_basis.to_scale(), target_base.matrix_basis.to_scale()))
        fist_unchanged = max(baseline_fingers.values(), default=0.0) < 1e-6
        numerical_gates = {
            "finite": finite,
            "target_scale_unchanged": scale_error < 1e-6,
            "non_arm_local_channels_unchanged": baseline_other_errors < 1e-6,
            "closed_fist_channels_unchanged": fist_unchanged,
            "no_runtime_constraints": runtime_constraints == 0,
            "bake_replay_error": bake_pose_error < 1e-5,
            "pose_jump": max_step < args.max_jump_radians,
            "glove_gap": max_glove_gap < args.max_glove_gap_delta,
        }
        passed = all(numerical_gates.values())
        report = {
            "schema": "mg1.direct-galaxy-walk-adapter-probe.v1",
            "input_blend": bpy.data.filepath,
            "source_rig": source.name,
            "source_action": source_action.name,
            "source_action_slot": source_slot.identifier,
            "source_action_curve_count": len(source_curves),
            "source_action_evaluation": "selected-slot FCurve evaluation at each integer frame",
            "source_channel_diagnostics": source_channel_samples,
            "source_arm_motion_radians": {
                name: max((quat_angle(values[0], value) for value in values[1:]),
                          default=0.0)
                for name, values in source_rest_deltas.items()},
            "source_arm_max_frame_step_radians": {
                name: max((quat_angle(a, b) for a, b in zip(values, values[1:])),
                          default=0.0)
                for name, values in source_rest_deltas.items()},
            "target_arm_excursion_radians_from_frame0": {
                name: max((quat_angle(desired_by_frame[0][name], matrices[name])
                           for matrices in desired_by_frame.values()), default=0.0)
                for name in ARM_OUTPUTS},
            "branch_relative_orientation_change_radians": {
                "left_upper_23_25": branch_relative_excursion(desired_by_frame, "bone_23", "bone_25"),
                "left_forearm_26_29": branch_relative_excursion(desired_by_frame, "bone_26", "bone_29"),
                "right_upper_58_60": branch_relative_excursion(desired_by_frame, "bone_58", "bone_60"),
                "right_forearm_61_64": branch_relative_excursion(desired_by_frame, "bone_61", "bone_64"),
            },
            "expected_pose_matrices": {
                str(frame): {name: rows(matrix) for name, matrix in matrices.items()}
                for frame, matrices in desired_by_frame.items()},
            "target_baseline_rig": target_base.name,
            "target_baseline_action": walk_action.name,
            "target_rest_bones": len(target_base.data.bones),
            "frame_range": [args.start, args.end],
            "target_arm_mapping": {"left": LEFT, "right": RIGHT},
            "target_outputs": list(ARM_OUTPUTS),
            "target_meshes": target_mesh_names,
            "object_scale": list(target.matrix_basis.to_scale()),
            "max_target_rotation_step_radians": max_step,
            "worst_target_step": worst_step,
            "max_per_bone_rotation_step_radians": jumps,
            "wrist_glove_gap_armature_units": {k: {"max": max(v), "min": min(v)}
                                                for k, v in glove_gaps.items()},
            "baseline_non_arm_basis_error": baseline_other_errors,
            "fist_basis_error": baseline_fingers,
            "bake_replay_matrix_error": bake_pose_error,
            "runtime_constraints": runtime_constraints,
            "scale_error": scale_error,
            "gates": numerical_gates,
            "passed": passed,
            "preview": os.path.abspath(args.preview) if passed else None,
            "limitations": [
                "This is a first direct-transfer map, not visual acceptance.",
                "The source action has no independently authored finger channels; target fists remain at baseline.",
                "Waluigi's arm branches are independently mapped but not yet represented by a dedicated corrective control rig.",
            ],
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, allow_nan=False)
            f.write("\n")
        if passed:
            visible = {target, *target_meshes}
            hide_reference_objects(visible)
            scene.frame_set(0)
            bpy.context.view_layer.update()
            # Save to a new path only; never save over the source workfile.
            bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
        else:
            print("Direct walk prototype rejected by numerical gates; preview not saved.")
        print(json.dumps({"report": os.path.abspath(args.report), "passed": passed,
                          "gates": numerical_gates,
                          "max_step": max_step, "max_glove_gap": max_glove_gap}))
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
        bpy.context.view_layer.update()


if __name__ == "__main__":
    main()
