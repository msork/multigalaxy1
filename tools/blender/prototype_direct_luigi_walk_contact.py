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
from mathutils import Euler, Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import duplicate_rig
from direct_galaxy_adapter import validate_mapping
from test_semantic_preview_noop import copy_action_slot, duplicate_mesh


LEFT = {
    "ShoulderL": ("bone_22",),
    "ArmL1": ("bone_23", "bone_24", "bone_25"),
    "ArmL2": ("bone_26", "bone_27", "bone_29"),
    "HandL": ("bone_30",),
}
RIGHT = {
    "ShoulderR": ("bone_57",),
    "ArmR1": ("bone_58", "bone_59", "bone_60"),
    "ArmR2": ("bone_61", "bone_62", "bone_64"),
    "HandR": ("bone_65",),
}
ARM_OUTPUTS = tuple(dict.fromkeys(t for m in (LEFT, RIGHT)
                                  for targets in m.values() for t in targets))
LEG_OUTPUTS = ("bone_6", "bone_7", "bone_8", "bone_9", "bone_10", "bone_11",
               "bone_13", "bone_14", "bone_15", "bone_16", "bone_17")
FINGER_OUTPUTS = tuple(f"bone_{n}" for n in list(range(31, 52)) + list(range(66, 87)))
ALL_OUTPUTS = tuple(dict.fromkeys(ARM_OUTPUTS + LEG_OUTPUTS + FINGER_OUTPUTS))
LATERAL_GAIN = 0.55


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
        if prop == "rotation_euler":
            curves_for_bone = [c for c in curves if c.data_path ==
                               f'pose.bones["{bone_name}"].rotation_euler']
            key_times = sorted({float(k.co.x) for c in curves_for_bone
                                for k in c.keyframe_points})
            left = max((t for t in key_times if t <= frame), default=key_times[0])
            right = min((t for t in key_times if t >= frame), default=key_times[-1])
            if right > left:
                e0 = [next(c for c in curves_for_bone if c.array_index == i).evaluate(left)
                      for i in range(3)]
                e1 = [next(c for c in curves_for_bone if c.array_index == i).evaluate(right)
                      for i in range(3)]
                q0 = Euler(e0, 'XYZ').to_quaternion()
                q1 = Euler(e1, 'XYZ').to_quaternion()
                raw_jump = max(abs(a - b) for a, b in zip(e0, e1))
                if raw_jump > 2.5 and quat_angle(q0, q1) < 0.2:
                    q = q0.slerp(q1, (frame - left) / (right - left))
                    pb.rotation_mode = 'QUATERNION'
                    pb.rotation_quaternion = q
                    continue
            pb.rotation_mode = 'XYZ'
        current = list(getattr(pb, prop))
        for index, value in enumerate(components):
            if value is not None:
                current[index] = value
        if prop == 'rotation_quaternion':
            pb.rotation_mode = 'QUATERNION'
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


def set_pose_matrix_from_armature_matrix(rig, name, desired, pose_matrices):
    bone = rig.data.bones[name]
    parent = bone.parent
    if parent:
        rest_local = parent.matrix_local.inverted_safe() @ bone.matrix_local
        basis = rest_local.inverted_safe() @ pose_matrices[parent.name].inverted_safe() @ desired
    else:
        basis = bone.matrix_local.inverted_safe() @ desired
    rig.pose.bones[name].matrix_basis = basis


def lateral_compress(v, gain=LATERAL_GAIN, cap=None):
    result = v.copy()
    result.x *= gain
    if cap is not None:
        result.x = max(-cap, min(cap, result.x))
    return result


def aim_bone_matrix(rig, bone_name, head, direction):
    bone = rig.data.bones[bone_name]
    rest_direction = (bone.tail_local - bone.head_local).normalized()
    align = rest_direction.rotation_difference(direction.normalized())
    rotation = (align @ bone.matrix_local.to_quaternion()).normalized()
    return Matrix.LocRotScale(head, rotation, (1.0, 1.0, 1.0))


def branch_relative_excursion(pose_by_frame, bone_a, bone_b):
    relatives = []
    for frame in sorted(pose_by_frame):
        qa = pose_by_frame[frame][bone_a].to_quaternion()
        qb = pose_by_frame[frame][bone_b].to_quaternion()
        relatives.append(qa.inverted() @ qb)
    return max((quat_angle(relatives[0], q) for q in relatives[1:]), default=0.0)


CONTROL_SKELETON = {
    "left": {"source": ("ShoulderL", "ArmL1", "ArmL2", "HandL"),
             "rests": ("bone_22", "bone_25", "bone_26", "bone_30"),
             "deforms": {"shoulder": ("bone_22",),
                         "upper": ("bone_23", "bone_24", "bone_25"),
                         "forearm": ("bone_26", "bone_27", "bone_29"),
                         "hand": ("bone_30",)}},
    "right": {"source": ("ShoulderR", "ArmR1", "ArmR2", "HandR"),
              "rests": ("bone_57", "bone_60", "bone_61", "bone_65"),
              "deforms": {"shoulder": ("bone_57",),
                          "upper": ("bone_58", "bone_59", "bone_60"),
                          "forearm": ("bone_61", "bone_62", "bone_64"),
                          "hand": ("bone_65",)}},
}


def calibrated_local_delta(source_rig, source_name, control, source_object_rotation,
                           target_object_rotation, target_parent_rest, target_rest):
    """Translate one Galaxy local rotation channel into a Waluigi control basis."""
    source_bone = source_rig.data.bones[source_name]
    source_parent = source_bone.parent
    source_local_rest = (source_parent.matrix_local.inverted_safe() @ source_bone.matrix_local
                         if source_parent else source_bone.matrix_local)
    source_basis = control.pose.bones[source_name].matrix_basis.to_quaternion()
    source_parent_delta = (source_local_rest.to_quaternion() @ source_basis @
                           source_local_rest.to_quaternion().inverted())
    world_delta = (source_object_rotation @ source_parent_delta.to_matrix() @
                   source_object_rotation.inverted())
    target_parent_delta = (target_object_rotation.inverted() @ world_delta @
                           target_object_rotation)
    target_local_rest = target_parent_rest.inverted_safe() @ target_rest
    target_basis = (target_local_rest.to_quaternion().inverted() @
                    target_parent_delta.to_quaternion() @ target_local_rest.to_quaternion())
    return Matrix.LocRotScale(Vector((0.0, 0.0, 0.0)), target_basis, (1.0, 1.0, 1.0))


def distribute_control_pose(target, target_pose_matrices, control_pose, control_rest, side):
    profile = CONTROL_SKELETON[side]
    segment_for_bone = {bone: segment for segment, bones in profile["deforms"].items()
                        for bone in bones}
    ordered = profile["deforms"]["shoulder"] + profile["deforms"]["upper"] + \
              profile["deforms"]["forearm"] + profile["deforms"]["hand"]
    for name in ordered:
        segment = segment_for_bone[name]
        target_rest = target.data.bones[name].matrix_local.copy()
        desired = control_pose[segment] @ control_rest[segment].inverted_safe() @ target_rest
        set_pose_rotation_from_armature_matrix(target, name, desired, target_pose_matrices)
        target_pose_matrices.clear()
        target_pose_matrices.update(pose_matrices_from_basis(target))
    return target_pose_matrices


TRAJECTORY_PROFILE = {
    "left": {"source_torso": "Spine2", "target_torso": "bone_21",
             "source_joints": ("ArmL1", "ArmL2", "HandL"),
             "target_controls": ("bone_22", "bone_25", "bone_26", "bone_30"),
             "target_elbow": "bone_25", "target_wrist": "bone_30",
             "upper_axis_bone": "bone_25", "forearm_axis_bone": "bone_26",
             "wrist_source": "HandL"},
    "right": {"source_torso": "Spine2", "target_torso": "bone_21",
              "source_joints": ("ArmR1", "ArmR2", "HandR"),
              "target_controls": ("bone_57", "bone_60", "bone_61", "bone_65"),
              "target_elbow": "bone_60", "target_wrist": "bone_65",
              "upper_axis_bone": "bone_60", "forearm_axis_bone": "bone_61",
              "wrist_source": "HandR"},
}


def twist_angle_about(q, axis):
    q = q.copy()
    q.normalize()
    if q.w < 0.0:
        q.negate()
    projection = Vector((q.x, q.y, q.z)).dot(axis.normalized())
    return 2.0 * math.atan2(projection, float(q.w))


def source_segment_twist(source, source_pose, torso_name, source_torso_pose,
                         start_name, end_name, segment_bone):
    torso_rest = source.data.bones[torso_name].matrix_local
    start_rest = source.data.bones[start_name].head_local
    end_rest = source.data.bones[end_name].head_local
    rest_start = torso_rest.inverted_safe() @ start_rest
    rest_end = torso_rest.inverted_safe() @ end_rest
    current_start = source_torso_pose.inverted_safe() @ source_pose[start_name].translation
    current_end = source_torso_pose.inverted_safe() @ source_pose[end_name].translation
    rest_axis = rest_end - rest_start
    current_axis = current_end - current_start
    q_swing = rest_axis.normalized().rotation_difference(current_axis.normalized())
    bone_rest_local = torso_rest.inverted_safe() @ source.data.bones[segment_bone].matrix_local
    bone_pose_local = source_torso_pose.inverted_safe() @ source_pose[segment_bone]
    delta = bone_pose_local.to_quaternion() @ bone_rest_local.to_quaternion().inverted()
    return twist_angle_about(q_swing.inverted() @ delta, rest_axis)


def trajectory_control_pose(source, source_pose, target, target_pose, source_object_rotation,
                            target_object_rotation, side):
    """Solve Waluigi FK controls from Luigi's torso-relative joint trajectory.

    Luigi's measured shoulder, elbow and wrist points determine the two target
    segment directions. Target lengths come from Waluigi rest geometry; the
    shoulder displacement and each segment direction are scaled independently.
    Segment swing is solved geometrically and source axial twist is restored.
    """
    profile = TRAJECTORY_PROFILE[side]
    torso_s = source.data.bones[profile["source_torso"]].matrix_local.copy()
    torso_t = target.data.bones[profile["target_torso"]].matrix_local.copy()
    torso_s_pose = source_pose[profile["source_torso"]]
    torso_t_pose = target_pose[profile["target_torso"]]
    source_names = profile["source_joints"]
    src_shoulder_bone, src_elbow_bone, src_wrist_bone = source_names
    shoulder_control, upper_control, forearm_control, hand_control = profile["target_controls"]
    source_rest_points = [source.data.bones[n].head_local.copy() for n in source_names]
    source_rest_local = [torso_s.inverted_safe() @ p for p in source_rest_points]
    source_current_local = [torso_s_pose.inverted_safe() @ source_pose[n].translation
                            for n in source_names]

    upper_target_rest_bone = target.data.bones[upper_control]
    forearm_target_rest_bone = target.data.bones[forearm_control]
    hand_target_rest_bone = target.data.bones[hand_control]
    target_rest_shoulder = torso_t.inverted_safe() @ upper_target_rest_bone.head_local
    target_rest_elbow = torso_t.inverted_safe() @ target.data.bones[profile["target_elbow"]].tail_local
    target_rest_wrist = torso_t.inverted_safe() @ hand_target_rest_bone.head_local

    src_upper_rest_len = (source_rest_local[1] - source_rest_local[0]).length
    src_lower_rest_len = (source_rest_local[2] - source_rest_local[1]).length
    tgt_upper_len = (target_rest_elbow - target_rest_shoulder).length
    tgt_lower_len = (target_rest_wrist - target_rest_elbow).length
    if min(src_upper_rest_len, src_lower_rest_len, tgt_upper_len, tgt_lower_len) <= 1e-8:
        raise RuntimeError(f"invalid rest segment lengths for {side} arm trajectory")
    scale_upper = tgt_upper_len / src_upper_rest_len
    scale_lower = tgt_lower_len / src_lower_rest_len
    scale_shoulder = (tgt_upper_len + tgt_lower_len) / (src_upper_rest_len + src_lower_rest_len)

    # Convert local torso coordinates through object orientation and torso rest
    # frames. This maps Z-up Galaxy anatomy into Y-up Waluigi without guessing
    # Euler axes or mirroring the right side.
    armature_alignment = target_object_rotation.inverted() @ source_object_rotation
    anatomical = torso_t.to_3x3().inverted() @ armature_alignment @ torso_s.to_3x3()
    shoulder_delta = anatomical @ (source_current_local[0] - source_rest_local[0]) * scale_shoulder
    shoulder_local = target_rest_shoulder + lateral_compress(shoulder_delta, cap=0.18)
    source_upper = source_current_local[1] - source_current_local[0]
    source_lower = source_current_local[2] - source_current_local[1]
    # Source segment vectors are in Luigi units (~15–18), so a cap in target
    # armature units would collapse them. Apply dimensionless lateral gain only.
    upper_local = lateral_compress(anatomical @ source_upper, cap=None)
    # Compress both position vectors, while using the uncompressed vector as a
    # fallback pole when a near-straight pose makes the bend plane singular.
    lower_original = anatomical @ source_lower
    lower_local = lateral_compress(lower_original, cap=None)
    if upper_local.length < 1e-8 or lower_local.length < 1e-8:
        raise RuntimeError(f"degenerate source joint trajectory for {side} arm")
    upper_dir_local = upper_local.normalized()
    source_elbow_angle = math.acos(max(-1.0, min(1.0,
        source_upper.normalized().dot(source_lower.normalized()))))
    lower_perp = lower_local - upper_dir_local * lower_local.dot(upper_dir_local)
    if lower_perp.length < 1e-8:
        lower_perp = lower_original - upper_dir_local * lower_original.dot(upper_dir_local)
    if lower_perp.length < 1e-8:
        lower_perp = Vector((0.0, 0.0, 1.0)).cross(upper_dir_local)
    lower_dir_local = (upper_dir_local * math.cos(source_elbow_angle) +
                       lower_perp.normalized() * math.sin(source_elbow_angle)).normalized()
    elbow_local = shoulder_local + upper_dir_local * tgt_upper_len
    wrist_local = elbow_local + lower_dir_local * tgt_lower_len

    shoulder_arm = torso_t_pose @ shoulder_local
    elbow_arm = torso_t_pose @ elbow_local
    wrist_arm = torso_t_pose @ wrist_local
    upper_dir_arm = torso_t_pose.to_3x3() @ (elbow_local - shoulder_local).normalized()
    forearm_dir_arm = torso_t_pose.to_3x3() @ (wrist_local - elbow_local).normalized()

    def segment_orientation(target_bone, direction_arm, source_segment_bone,
                            source_start, source_end):
        rest_bone = target.data.bones[target_bone]
        rest_direction = (rest_bone.tail_local - rest_bone.head_local).normalized()
        align = rest_direction.rotation_difference(direction_arm.normalized())
        base_rotation = align @ rest_bone.matrix_local.to_quaternion()
        twist = source_segment_twist(source, source_pose, profile["source_torso"],
                                     torso_s_pose, source_start, source_end,
                                     source_segment_bone)
        twist_q = Quaternion(direction_arm.normalized(), twist)
        return (twist_q @ base_rotation).normalized(), twist

    upper_rotation, upper_twist = segment_orientation(
        upper_control, upper_dir_arm, src_shoulder_bone,
        src_shoulder_bone, src_elbow_bone)
    forearm_rotation, forearm_twist = segment_orientation(
        forearm_control, forearm_dir_arm, src_elbow_bone,
        src_elbow_bone, src_wrist_bone)
    upper_pose = Matrix.LocRotScale(shoulder_arm, upper_rotation, (1, 1, 1))
    forearm_pose = Matrix.LocRotScale(elbow_arm, forearm_rotation, (1, 1, 1))

    rest_shoulder_control = target.data.bones[shoulder_control].matrix_local.copy()
    rest_upper_control = upper_target_rest_bone.matrix_local.copy()
    rest_forearm_control = forearm_target_rest_bone.matrix_local.copy()
    rest_hand_control = hand_target_rest_bone.matrix_local.copy()
    shoulder_pose = upper_pose @ rest_upper_control.inverted_safe() @ rest_shoulder_control

    wrist_basis = calibrated_local_delta(
        source, profile["wrist_source"], source,
        source_object_rotation, target_object_rotation,
        rest_forearm_control, rest_hand_control).to_quaternion()
    hand_rest_local = rest_forearm_control.inverted_safe() @ rest_hand_control
    hand_pose = forearm_pose @ hand_rest_local @ Matrix.LocRotScale(
        Vector((0, 0, 0)), wrist_basis, (1, 1, 1))
    hand_pose.translation = wrist_arm

    control_pose = {"shoulder": shoulder_pose, "upper": upper_pose,
                    "forearm": forearm_pose, "hand": hand_pose}
    control_rest = {"shoulder": rest_shoulder_control, "upper": rest_upper_control,
                    "forearm": rest_forearm_control, "hand": rest_hand_control}
    diagnostics = {
        "source_torso_local_joints": [list(p) for p in source_current_local],
        "source_torso_local_rest_joints": [list(p) for p in source_rest_local],
        "target_torso_local_joints": [list(shoulder_local), list(elbow_local), list(wrist_local)],
        "target_torso_local_rest_joints": [list(target_rest_shoulder),
                                            list(target_rest_elbow), list(target_rest_wrist)],
        "source_to_target_torso_basis": [[float(anatomical[r][c]) for c in range(3)]
                                          for r in range(3)],
        "source_upper_length": src_upper_rest_len,
        "source_forearm_length": src_lower_rest_len,
        "target_upper_length": tgt_upper_len,
        "target_forearm_length": tgt_lower_len,
        "source_total_length": src_upper_rest_len + src_lower_rest_len,
        "target_total_length": tgt_upper_len + tgt_lower_len,
        "source_elbow_flexion_radians": math.acos(max(-1.0, min(1.0,
            source_upper.normalized().dot(source_lower.normalized())))),
        "lateral_gain": LATERAL_GAIN,
        "target_torso_local_lateral_excursion": [
            float(shoulder_local.x - target_rest_shoulder.x),
            float(elbow_local.x - target_rest_shoulder.x),
            float(wrist_local.x - target_rest_shoulder.x)],
        "source_forearm_twist_radians": forearm_twist,
        "source_upper_twist_radians": upper_twist,
        "source_arm_plane_normal": list(source_upper.cross(source_lower).normalized()),
        "mapped_wrist_basis_quaternion": list(wrist_basis),
    }
    return control_pose, control_rest, diagnostics


def normalized_vec_error(a, b):
    return (Vector(a) - Vector(b)).length


def rms(values):
    return math.sqrt(sum(float(v) ** 2 for v in values) / len(values)) if values else 0.0


def scalar_range(values):
    return [min(values), max(values)] if values else [0.0, 0.0]


def motion_retention(source_values, target_values):
    source_excursion = max(source_values) - min(source_values) if source_values else 0.0
    target_excursion = max(target_values) - min(target_values) if target_values else 0.0
    return {"source_excursion": source_excursion,
            "target_excursion": target_excursion,
            "retention_ratio": target_excursion / source_excursion
                if source_excursion > 1e-8 else None}


def trajectory_actual_points(target, pose_matrices, side):
    profile = TRAJECTORY_PROFILE[side]
    torso = pose_matrices[profile["target_torso"]]
    shoulder_bone = profile["target_elbow"]
    forearm_bone = "bone_27" if side == "left" else "bone_62"
    shoulder = pose_matrices[shoulder_bone].translation.copy()
    elbow = pose_matrices[shoulder_bone] @ Vector((0.0, target.data.bones[shoulder_bone].length, 0.0))
    wrist = pose_matrices[forearm_bone] @ Vector((0.0, target.data.bones[forearm_bone].length, 0.0))
    inv_torso = torso.inverted_safe()
    return [inv_torso @ shoulder, inv_torso @ elbow, inv_torso @ wrist]


def target_segment_twist(target, pose_matrices, side):
    profile = TRAJECTORY_PROFILE[side]
    torso_name = profile["target_torso"]
    shoulder_name, elbow_name, wrist_name = profile["target_controls"][1:]
    torso_rest = target.data.bones[torso_name].matrix_local
    torso_pose = pose_matrices[torso_name]
    rest_start = torso_rest.inverted_safe() @ target.data.bones[shoulder_name].head_local
    rest_end = torso_rest.inverted_safe() @ target.data.bones[elbow_name].head_local
    rest_wrist = torso_rest.inverted_safe() @ target.data.bones["bone_30" if side == "left" else "bone_65"].head_local
    current_points = trajectory_actual_points(target, pose_matrices, side)
    rest_axis = rest_wrist - rest_end
    current_axis = current_points[2] - current_points[1]
    swing = rest_axis.normalized().rotation_difference(current_axis.normalized())
    bone_rest_local = torso_rest.inverted_safe() @ target.data.bones[elbow_name].matrix_local
    bone_pose_local = torso_pose.inverted_safe() @ pose_matrices[elbow_name]
    delta = bone_pose_local.to_quaternion() @ bone_rest_local.to_quaternion().inverted()
    return twist_angle_about(swing.inverted() @ delta, rest_axis)


MESH_SEAMS = {
    "left_shoulder": ("bone_23", "bone_25"),
    "left_elbow": ("bone_25", "bone_29"),
    "left_wrist_forearm": ("bone_29", "bone_27"),
    "right_shoulder": ("bone_58", "bone_60"),
    "right_elbow": ("bone_60", "bone_64"),
    "right_wrist_forearm": ("bone_64", "bone_62"),
}


def evaluated_world_vertices(obj):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return [ev.matrix_world @ v.co for v in mesh.vertices]
    finally:
        ev.to_mesh_clear()


def mesh_group_weights(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    return [{names[a.group]: float(a.weight) for a in v.groups if a.weight > 0.01}
            for v in obj.data.vertices]


def weighted_mesh_group_centroid(meshes, group):
    accum, total = Vector(), 0.0
    for obj in meshes:
        weights = mesh_group_weights(obj)
        points = evaluated_world_vertices(obj)
        for point, row in zip(points, weights):
            weight = row.get(group, 0.0)
            if weight > 0.01:
                accum += point * weight
                total += weight
    return accum / total if total else None


def weighted_mesh_group_centroids(meshes, groups):
    accum = {g: Vector() for g in groups}
    totals = {g: 0.0 for g in groups}
    for obj in meshes:
        weights = mesh_group_weights(obj)
        points = evaluated_world_vertices(obj)
        for point, row in zip(points, weights):
            for group in groups:
                weight = row.get(group, 0.0)
                if weight > 0.01:
                    accum[group] += point * weight
                    totals[group] += weight
    return {g: accum[g] / totals[g] if totals[g] else None for g in groups}


def mesh_nearest_gap(points_a, points_b):
    if not points_a or not points_b:
        return None
    from mathutils.kdtree import KDTree
    tree = KDTree(len(points_b))
    for i, p in enumerate(points_b):
        tree.insert(p, i)
    tree.balance()
    return min(tree.find(p)[2] for p in points_a)


def mesh_seam_report(meshes):
    result = {}
    for obj in meshes:
        verts, weights = evaluated_world_vertices(obj), mesh_group_weights(obj)
        row = {}
        for seam, (group_a, group_b) in MESH_SEAMS.items():
            points_a = [p for p, w in zip(verts, weights)
                        if w and max(w, key=w.get) == group_a]
            points_b = [p for p, w in zip(verts, weights)
                        if w and max(w, key=w.get) == group_b]
            crossing = []
            for edge in obj.data.edges:
                a, b = edge.vertices
                wa, wb = weights[a], weights[b]
                da = max(wa, key=wa.get) if wa else ""
                db = max(wb, key=wb.get) if wb else ""
                if {da, db} == {group_a, group_b}:
                    crossing.append((verts[a] - verts[b]).length)
            crossing.sort()
            row[seam] = {"min_surface_gap": mesh_nearest_gap(points_a, points_b),
                         "cross_weight_edge_p95": (crossing[min(int(0.95 * (len(crossing)-1)),
                                                                 len(crossing)-1)]
                                                    if crossing else None),
                         "vertices_a": len(points_a), "vertices_b": len(points_b)}
        result[obj.name] = row
    return result


def max_seam_delta(candidate_rows, baseline_rows):
    max_gap_delta = 0.0
    max_edge_ratio_delta = 0.0
    worst_gap = {"delta": 0.0, "mesh": None, "seam": None}
    worst_edge = {"relative_delta": 0.0, "mesh": None, "seam": None}
    candidate_names = sorted(candidate_rows)
    baseline_names = sorted(baseline_rows)
    for name, base_name in zip(candidate_names, baseline_names):
        seams = candidate_rows[name]
        for seam, data in seams.items():
            base = baseline_rows[base_name][seam]["min_surface_gap"]
            gap = data["min_surface_gap"]
            if gap is None and base is None:
                continue
            delta = abs(gap - base) if gap is not None and base is not None else 1.0e9
            if delta > max_gap_delta:
                max_gap_delta = delta
                worst_gap = {"delta": delta, "mesh": name, "seam": seam,
                         "candidate_gap": gap, "baseline_gap": base}
            edge_base = baseline_rows[base_name][seam]["cross_weight_edge_p95"]
            edge_candidate = data["cross_weight_edge_p95"]
            if edge_candidate is None and edge_base is None:
                continue
            edge_ratio_delta = (abs(edge_candidate - edge_base) / max(abs(edge_base), 1.0e-9)
                                if edge_candidate is not None and edge_base is not None else 1.0e9)
            if edge_ratio_delta > max_edge_ratio_delta:
                max_edge_ratio_delta = edge_ratio_delta
                worst_edge = {"relative_delta": edge_ratio_delta, "mesh": name,
                              "seam": seam, "candidate_edge_p95": edge_candidate,
                              "baseline_edge_p95": edge_base}
    return max_gap_delta, worst_gap, max_edge_ratio_delta, worst_edge


def hide_reference_objects(visible_set):
    for obj in bpy.data.objects:
        show = obj in visible_set
        obj.hide_render = not show
        obj.hide_viewport = not show
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

    frames = [i * 0.25 for i in range(args.start * 4, args.end * 4 + 1)]
    if args.start != 0 or args.end != 60:
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
                                     "Waluigi_DirectGalaxyWalk_v4_ContactNarrowFists")
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
    # Search real native hand poses by evaluated, weighted fingertip geometry.
    # This avoids declaring a pose a fist from quaternion magnitudes alone.
    fist_rig = duplicate_rig(target_base, "DirectWalk_NativeFist_Sampler")
    fist_basis = {}
    fist_pose_sources = {}
    baseline_probe = duplicate_rig(target_base, "DirectWalk_Baseline_MeshProbe")
    baseline_probe.matrix_basis = object_basis(target_base)
    baseline_probe.animation_data_clear()
    baseline_meshes = [duplicate_mesh(o, baseline_probe, collection, "_DirectWalk_Baseline_")
                       for o in mesh_sources]
    for mesh in baseline_meshes:
        mesh.hide_render = True
        mesh.hide_set(True)
    baseline_probe.hide_render = True
    baseline_probe.hide_set(True)
    probe_modifier_states = {
        obj.name: [(modifier, modifier.show_viewport) for modifier in obj.modifiers]
        for obj in baseline_meshes + target_meshes
    }

    srot = object_basis(control).to_3x3().to_quaternion().to_matrix()
    trot = object_basis(target).to_3x3().to_quaternion().to_matrix()
    desired_by_frame = {}
    mesh_gaps_by_frame = {}
    jumps = {name: 0.0 for name in ALL_OUTPUTS}
    previous = {}
    semantic_bones = ("ShoulderL", "ArmL1", "ArmL2", "HandL",
                      "ShoulderR", "ArmR1", "ArmR2", "HandR")
    source_rest_deltas = {key: [] for key in semantic_bones}
    source_channel_samples = {}
    trajectory_rows = {"left": [], "right": []}
    trajectory_world_samples = {}
    glove_gaps = {"left": [], "right": []}
    baseline_fingers = {}
    baseline_other_errors = 0.0
    worst_step = {"radians": 0.0, "frame": None, "bone": None}
    foot_rows = {"left": [], "right": []}
    foot_anchors = {"left": None, "right": None}
    foot_vector_anchors = {"left": None, "right": None}
    source_hip_reference = None
    armature_alignment = target.matrix_basis.to_3x3().inverted() @ control.matrix_basis.to_3x3()
    source_leg_lengths = {}
    target_leg_lengths = {}
    for side, thigh, shin, foot in (("left", "bone_7", "bone_8", "FootL"),
                                    ("right", "bone_13", "bone_14", "FootR")):
        source_leg_lengths[side] = ((control.data.bones["Hip"].head_local -
                                    control.data.bones[foot].head_local).length)
        target_leg_lengths[side] = ((target.data.bones[thigh].length +
                                    target.data.bones[shin].length) *
                                   target.matrix_basis.to_scale().x)
    leg_scale = {s: target_leg_lengths[s] / max(source_leg_lengths[s], 1e-8)
                 for s in source_leg_lengths}
    source_hip_reference = None
    previous_stance = {"left": False, "right": False}
    foot_surface_samples = {"left": [], "right": []}
    fist_shape_samples = {"left": [], "right": []}
    floor_correction_world = {"left": 2.265, "right": 1.690}
    scene.frame_set(0)
    evaluate_source_curves(target, target_baseline_curves, 0.0)
    bpy.context.view_layer.update()
    baseline_fist_scores = {}
    for side, palm, tips in (("left", "bone_30", ("bone_33", "bone_37", "bone_41", "bone_45", "bone_49")),
                             ("right", "bone_65", ("bone_68", "bone_72", "bone_76", "bone_80", "bone_84"))):
        centers = weighted_mesh_group_centroids(target_meshes, (palm, *tips))
        palm_point = centers[palm]
        tip_points = [centers[name] for name in tips]
        baseline_fist_scores[side] = (sum((point - palm_point).length for point in tip_points if point is not None) /
                                      max(1, sum(point is not None for point in tip_points)))
    target_action_for_fist = target.animation_data.action
    target_slot_for_fist = target.animation_data.action_slot
    target.animation_data.action = None
    native_fist_candidates = {}
    baseline_fist_basis = {name: target.pose.bones[name].matrix_basis.copy()
                           for name in FINGER_OUTPUTS}
    fist_profiles = (("left", "bone_30", ("bone_31", "bone_32", "bone_33", "bone_34",
        "bone_35", "bone_36", "bone_37", "bone_38", "bone_39", "bone_40", "bone_41",
        "bone_42", "bone_43", "bone_44", "bone_45", "bone_46", "bone_47", "bone_48",
        "bone_49", "bone_50", "bone_51"), ("bone_33", "bone_37", "bone_41", "bone_45", "bone_49")),
        ("right", "bone_65", tuple(f"bone_{n}" for n in range(66, 87)),
         ("bone_68", "bone_72", "bone_76", "bone_80", "bone_84")))
    best_fist_score = dict(baseline_fist_scores)
    best_fist_basis = {}
    for action_name in ("sequence_10.003", "sequence_15.003", "sequence_24"):
        candidate_action = bpy.data.actions.get(action_name)
        if candidate_action is None:
            continue
        candidate_slot = action_slot(candidate_action, "OBArmature.004")
        candidate_curves = curves_for_slot(candidate_action, candidate_slot)
        sample_times = sorted({float(candidate_action.frame_range[0]),
                               (float(candidate_action.frame_range[0]) + float(candidate_action.frame_range[1])) * 0.5,
                               float(candidate_action.frame_range[1])})
        for sample_time in sample_times:
            evaluate_source_curves(fist_rig, candidate_curves, sample_time)
            for side, palm, bones, tips in fist_profiles:
                for name in bones:
                    target.pose.bones[name].matrix_basis = fist_rig.pose.bones[name].matrix_basis
                bpy.context.view_layer.update()
                centers = weighted_mesh_group_centroids(target_meshes, (palm, *tips))
                palm_point = centers[palm]
                tip_points = [centers[name] for name in tips]
                score = (sum((point - palm_point).length for point in tip_points if point is not None) /
                         max(1, sum(point is not None for point in tip_points)))
                native_fist_candidates[f"{side}:{action_name}@{sample_time:g}"] = score
                if score < best_fist_score[side] - 1e-4:
                    best_fist_score[side] = score
                    best_fist_basis[side] = {name: fist_rig.pose.bones[name].matrix_basis.copy()
                                             for name in bones}
                    fist_pose_sources[side] = {"action": action_name, "frame": sample_time,
                                               "weighted_tip_to_palm_mean_world": score}
    for side, palm, bones, tips in fist_profiles:
        selected = best_fist_basis.get(side)
        for name in bones:
            if selected:
                fist_basis[name] = selected[name]
            else:
                fist_basis[name] = baseline_fist_basis[name]
    # The user-approved v5 walk contains a visually validated closed left fist.
    # Reuse its production-basis local rotations, then mirror the rotation
    # deltas through character rest space for the right hand.
    fist_reference_path = os.path.join(HERE, "profiles", "waluigi_v5_closed_fist.json")
    if not os.path.isfile(fist_reference_path):
        raise RuntimeError("validated v5 closed-fist reference is missing")
    with open(fist_reference_path, "r", encoding="utf-8") as f:
        approved_fist = json.load(f)["quaternion_wxyz_by_bone"]
    reflection = Matrix.Diagonal(Vector((-1.0, 1.0, 1.0)))
    for left_index in range(31, 52):
        left_name, right_name = f"bone_{left_index}", f"bone_{left_index + 35}"
        q_left = Quaternion(approved_fist[left_name])
        fist_basis[left_name] = Matrix.LocRotScale(
            baseline_fist_basis[left_name].translation, q_left,
            baseline_fist_basis[left_name].to_scale())
        left_bone, right_bone = target.data.bones[left_name], target.data.bones[right_name]
        left_rest_local = (left_bone.parent.matrix_local.inverted_safe() @ left_bone.matrix_local
                           if left_bone.parent else left_bone.matrix_local)
        right_rest_local = (right_bone.parent.matrix_local.inverted_safe() @ right_bone.matrix_local
                            if right_bone.parent else right_bone.matrix_local)
        left_delta = (left_rest_local.to_3x3() @ q_left.to_matrix() @
                      left_rest_local.to_3x3().inverted_safe())
        mirrored_delta = reflection @ left_delta @ reflection
        right_rotation = (right_rest_local.to_3x3().inverted_safe() @ mirrored_delta @
                          right_rest_local.to_3x3()).to_quaternion()
        fist_basis[right_name] = Matrix.LocRotScale(
            baseline_fist_basis[right_name].translation, right_rotation,
            baseline_fist_basis[right_name].to_scale())
    fist_pose_sources = {"left": {"source": "validated v5 closed-fist pose",
        "source_action": "Waluigi_LuigiWalk_SemanticLeftArm_Elbow_v1", "frame": 0},
        "right": {"source": "left v5 fist mirrored through production rest-space homologs",
                  "mapping": "bone_31..bone_51 -> bone_66..bone_86"}}
    fist_rig_data = fist_rig.data
    bpy.data.objects.remove(fist_rig, do_unlink=True)
    if fist_rig_data.users == 0:
        bpy.data.armatures.remove(fist_rig_data)
    for name, basis in fist_basis.items():
        target.pose.bones[name].matrix_basis = basis
    target.animation_data.action = target_action_for_fist
    target.animation_data.action_slot = target_slot_for_fist

    try:
        for frame in frames:
            scene.frame_set(int(frame), subframe=frame - int(frame))
            bpy.context.view_layer.update()
            evaluate_source_curves(control, source_curves, frame)
            source_pose_matrices = pose_matrices_from_basis(control)
            if source_hip_reference is None:
                source_hip_reference = source_pose_matrices["Hip"].translation.copy()
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
            # Bring Luigi's pelvis bob into Waluigi's object-local up axis,
            # scaled by the same anatomical leg ratio used for planted feet.
            root_delta = armature_alignment @ (
                source_pose_matrices["Hip"].translation - source_hip_reference) * \
                ((leg_scale["left"] + leg_scale["right"]) * 0.5)
            root_pose = target_pose_matrices["bone_6"].copy()
            root_pose.translation += root_delta
            set_pose_matrix_from_armature_matrix(target, "bone_6", root_pose,
                                                 target_pose_matrices)
            target_pose_matrices = pose_matrices_from_basis(target)
            frame_trajectory = {}
            for side in ("left", "right"):
                controls, rests, trajectory = trajectory_control_pose(
                    control, source_pose_matrices, target, target_pose_matrices,
                    srot, trot, side)
                target_pose_matrices = distribute_control_pose(
                    target, target_pose_matrices, controls, rests, side)
                frame_trajectory[side] = trajectory

            # Scale Luigi's hip/knee/ankle paths to Waluigi's segment lengths.
            # During source stance, hold the source ankle anchor while the
            # target pelvis bobs; a two-segment analytic solve supplies the knee.
            for side, source_knee, source_foot, upper, lower, foot_bone in (
                    ("left", "LegL2", "FootL", "bone_7", "bone_8", "bone_9"),
                    ("right", "LegR2", "FootR", "bone_13", "bone_14", "bone_15")):
                is_stance = ((29.0 <= frame <= 34.0) if side == "left" else
                             (0.0 <= frame <= 3.0 or 59.0 <= frame <= 60.0))
                source_ankle = source_pose_matrices[source_foot].translation.copy()
                if is_stance and not previous_stance[side]:
                    foot_anchors[side] = source_ankle.copy()
                    foot_vector_anchors[side] = (source_pose_matrices[source_foot] @
                        Vector((0, control.data.bones[source_foot].length, 0))) - source_ankle
                if not is_stance:
                    foot_anchors[side] = None
                    foot_vector_anchors[side] = None
                previous_stance[side] = is_stance
                anchor = foot_anchors[side] if is_stance and foot_anchors[side] is not None else source_ankle
                source_hip = source_pose_matrices["Hip"].translation
                target_hip = target_pose_matrices[upper].translation.copy()
                mapped_ankle_delta = armature_alignment @ (anchor - source_hip) * leg_scale[side]
                ankle_target = target_hip + mapped_ankle_delta
                if is_stance:
                    ankle_target.y -= floor_correction_world[side] / max(
                        target.matrix_basis.to_scale().x, 1e-8)
                mapped_knee = target_hip + armature_alignment @ (
                    source_pose_matrices[source_knee].translation - source_hip) * leg_scale[side]
                upper_len = target.data.bones[upper].length
                lower_len = target.data.bones[lower].length
                hip_to_ankle = ankle_target - target_hip
                distance = max(1e-5, hip_to_ankle.length)
                max_reach = upper_len + lower_len - 1e-5
                distance = min(max_reach, max(abs(upper_len - lower_len) + 1e-5, distance))
                axis = hip_to_ankle.normalized()
                along = (upper_len * upper_len - lower_len * lower_len + distance * distance) / (2.0 * distance)
                height = math.sqrt(max(0.0, upper_len * upper_len - along * along))
                pole = mapped_knee - target_hip
                pole -= axis * pole.dot(axis)
                if pole.length < 1e-7:
                    pole = target.data.bones[upper].matrix_local.to_3x3() @ Vector((1, 0, 0))
                    pole -= axis * pole.dot(axis)
                knee = target_hip + axis * along + pole.normalized() * height
                upper_matrix = aim_bone_matrix(target, upper, target_hip, knee - target_hip)
                set_pose_matrix_from_armature_matrix(target, upper, upper_matrix,
                                                     target_pose_matrices)
                target_pose_matrices = pose_matrices_from_basis(target)
                lower_matrix = aim_bone_matrix(target, lower, knee, ankle_target - knee)
                set_pose_matrix_from_armature_matrix(target, lower, lower_matrix,
                                                     target_pose_matrices)
                target_pose_matrices = pose_matrices_from_basis(target)
                source_toe = source_pose_matrices[source_foot] @ Vector((0, control.data.bones[source_foot].length, 0))
                source_foot_vec = (foot_vector_anchors[side] if is_stance and
                    foot_vector_anchors[side] is not None else source_toe - source_ankle)
                mapped_foot_vec = armature_alignment @ source_foot_vec
                foot_matrix = aim_bone_matrix(target, foot_bone, ankle_target, mapped_foot_vec)
                set_pose_matrix_from_armature_matrix(target, foot_bone, foot_matrix,
                                                     target_pose_matrices)
                target_pose_matrices = pose_matrices_from_basis(target)
                ankle_world = target.matrix_basis @ ankle_target
                foot_rows[side].append({"frame": frame, "stance": is_stance,
                    "ankle_armature": list(ankle_target), "ankle_world": list(ankle_world),
                    "knee_armature": list(knee), "mapped_source_ankle": list(anchor),
                    "segment_lengths": [upper_len, lower_len]})

            for finger_name, basis in fist_basis.items():
                target.pose.bones[finger_name].matrix_basis = basis
            target_pose_matrices = pose_matrices_from_basis(target)
            bpy.context.view_layer.update()
            for target_name in ALL_OUTPUTS:
                q = target_pose_matrices[target_name].to_quaternion()
                if target_name in previous:
                    step = quat_angle(previous[target_name], q)
                    jumps[target_name] = max(jumps[target_name], step)
                    if step > worst_step["radians"]:
                        worst_step = {"radians": step, "frame": frame,
                                      "bone": target_name}
                previous[target_name] = q.copy()
            bpy.context.view_layer.update()
            for name in ALL_OUTPUTS:
                pb = target.pose.bones[name]
                pb.rotation_mode = 'QUATERNION'
                pb.keyframe_insert(data_path="rotation_quaternion", frame=frame,
                                   group="Direct Galaxy Adapter")
                pb.keyframe_insert(data_path="location", frame=frame,
                                   group="Direct Galaxy Adapter")
                pb.keyframe_insert(data_path="scale", frame=frame,
                                   group="Direct Galaxy Adapter")
            desired_by_frame[frame] = {n: target_pose_matrices[n].copy()
                                       for n in ALL_OUTPUTS}
            for side in ("left", "right"):
                profile = TRAJECTORY_PROFILE[side]
                td = frame_trajectory[side]
                anatomical = Matrix(td["source_to_target_torso_basis"])
                source_points = [Vector(p) for p in td["source_torso_local_joints"]]
                source_rest_points = [Vector(p) for p in td["source_torso_local_rest_joints"]]
                expected_points = [Vector(p) for p in td["target_torso_local_joints"]]
                target_rest_points = [Vector(p) for p in td["target_torso_local_rest_joints"]]
                actual_points = trajectory_actual_points(target, target_pose_matrices, side)
                source_total = td["source_total_length"]
                target_total = td["target_total_length"]
                src_upper = source_points[1] - source_points[0]
                src_lower = source_points[2] - source_points[1]
                tgt_upper = actual_points[1] - actual_points[0]
                tgt_lower = actual_points[2] - actual_points[1]
                mapped_src_shoulder = anatomical @ (source_points[0] - source_rest_points[0]) / source_total
                actual_tgt_shoulder = (actual_points[0] - target_rest_points[0]) / target_total
                mapped_src_elbow = anatomical @ src_upper / source_total
                actual_tgt_elbow = tgt_upper / target_total
                mapped_src_wrist = anatomical @ (source_points[2] - source_points[0]) / source_total
                actual_tgt_wrist = (actual_points[2] - actual_points[0]) / target_total
                source_forward = mapped_src_wrist.z
                target_forward = actual_tgt_wrist.z
                source_lateral = (anatomical @ (source_points[2] - source_points[0])).x / source_total
                target_lateral = (actual_points[2] - actual_points[0]).x / target_total
                source_flex = td["source_elbow_flexion_radians"]
                target_flex = math.acos(max(-1.0, min(1.0,
                    tgt_upper.normalized().dot(tgt_lower.normalized()))))
                mapped_plane = (anatomical @ src_upper).cross(anatomical @ src_lower).normalized()
                target_plane = tgt_upper.cross(tgt_lower).normalized()
                plane_error = math.acos(max(-1.0, min(1.0,
                    mapped_plane.dot(target_plane))))
                target_twist = target_segment_twist(target, target_pose_matrices, side)
                source_twist = td["source_forearm_twist_radians"]
                hand_name = "bone_30" if side == "left" else "bone_65"
                forearm_name = "bone_26" if side == "left" else "bone_61"
                target_rest_hand = target.data.bones[hand_name].matrix_local
                target_rest_forearm = target.data.bones[forearm_name].matrix_local
                expected_wrist_relative = ((target_rest_forearm.inverted_safe() @
                    target_rest_hand).to_quaternion() @
                    Quaternion(td["mapped_wrist_basis_quaternion"])).normalized()
                actual_wrist_relative = (target_pose_matrices[forearm_name].to_quaternion().inverted() @
                                         target_pose_matrices[hand_name].to_quaternion()).normalized()
                wrist_orientation_error = quat_angle(expected_wrist_relative,
                                                     actual_wrist_relative)
                row = {
                    "frame": frame,
                    "source_shoulder_excursion_normalized": list(mapped_src_shoulder),
                    "target_shoulder_excursion_normalized": list(actual_tgt_shoulder),
                    "source_elbow_vector_normalized": list(mapped_src_elbow),
                    "target_elbow_vector_normalized": list(actual_tgt_elbow),
                    "source_wrist_vector_normalized": list(mapped_src_wrist),
                    "target_wrist_vector_normalized": list(actual_tgt_wrist),
                    "normalized_shoulder_error": normalized_vec_error(mapped_src_shoulder,
                                                                        actual_tgt_shoulder),
                    "normalized_elbow_trajectory_error": normalized_vec_error(mapped_src_elbow,
                                                                                 actual_tgt_elbow),
                    "normalized_wrist_trajectory_error": normalized_vec_error(mapped_src_wrist,
                                                                                actual_tgt_wrist),
                    "source_forward_backward": source_forward,
                    "target_forward_backward": target_forward,
                    "source_lateral_reach_normalized": source_lateral,
                    "target_lateral_reach_normalized": target_lateral,
                    "source_elbow_flexion_radians": source_flex,
                    "target_elbow_flexion_radians": target_flex,
                    "elbow_flexion_error_radians": abs(source_flex - target_flex),
                    "source_forearm_roll_radians": source_twist,
                    "target_forearm_roll_radians": target_twist,
                    "forearm_roll_error_radians": abs(source_twist - target_twist),
                    "arm_plane_orientation_error_radians": plane_error,
                    "wrist_relative_orientation_error_radians": wrist_orientation_error,
                    "source_torso_local_joints": td["source_torso_local_joints"],
                    "target_torso_local_joints": [list(p) for p in actual_points],
                }
                trajectory_rows[side].append(row)
                if frame in (0, 15, 30, 45, 60):
                    src_pts_arm = [source_pose_matrices[n].translation for n in profile["source_joints"]]
                    target_pts_arm = [target_pose_matrices[profile["target_controls"][1]].translation,
                        target_pose_matrices[profile["target_elbow"]] @ Vector((0, target.data.bones[profile["target_elbow"]].length, 0)),
                        target_pose_matrices["bone_27" if side == "left" else "bone_62"] @
                            Vector((0, target.data.bones["bone_27" if side == "left" else "bone_62"].length, 0))]
                    trajectory_world_samples.setdefault(side, {})[str(frame)] = {
                        "source_world_shoulder_elbow_wrist": [list((object_basis(source) @ p))
                                                               for p in src_pts_arm],
                        "target_world_shoulder_elbow_wrist": [list((object_basis(target) @ p))
                                                               for p in target_pts_arm],
                    }
            frame_trajectory = None
            # Verify actual armature-skinned mesh seams against the unchanged
            # walk baseline for every frame, using temporary duplicate meshes.
            for name, basis in pose_snapshot.items():
                baseline_probe.pose.bones[name].matrix_basis = basis
            bpy.context.view_layer.update()
            for obj in baseline_meshes + target_meshes:
                for modifier in obj.modifiers:
                    if modifier.type != 'ARMATURE':
                        modifier.show_viewport = False
            if abs(frame - round(frame)) < 1e-6:
                mesh_gaps_by_frame[str(int(round(frame)))] = {
                    "baseline": mesh_seam_report(baseline_meshes),
                    "candidate": mesh_seam_report(target_meshes),
                }
            for states in probe_modifier_states.values():
                for modifier, was_visible in states:
                    modifier.show_viewport = was_visible
            for semantic_bone in semantic_bones:
                src_rest = control.data.bones[semantic_bone].matrix_local
                delta = source_pose_matrices[semantic_bone].to_3x3() @ src_rest.to_3x3().inverted()
                source_rest_deltas[semantic_bone].append(delta.to_quaternion())
            # Keep old endpoint diagnostics for comparison only; they are not
            # used as the glove attachment acceptance criterion.
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
                    mat_err(fist_basis[hand], target.pose.bones[hand].matrix_basis))
            # Non-arm channels must remain exactly on the approved baseline.
            for name, before in pose_snapshot.items():
                if name in ALL_OUTPUTS:
                    continue
                # Arm descendants inherit the arm pose; compare local basis,
                # which should remain baseline-identical on untouched channels.
                err = mat_err(before, target.pose.bones[name].matrix_basis)
                baseline_other_errors = max(baseline_other_errors, err)

        for curve in fcurves(target_action):
            is_output_curve = any(f'pose.bones["{name}"]' in curve.data_path
                                  for name in ALL_OUTPUTS)
            if is_output_curve:
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
            scene.frame_set(int(frame), subframe=frame - int(frame))
            evaluate_source_curves(target, list(fcurves(target_action)), frame)
            bpy.context.view_layer.update()
            replay_pose_matrices = pose_matrices_from_basis(target)
            for name, intended in desired_by_frame[frame].items():
                actual = replay_pose_matrices[name]
                bake_pose_error = max(bake_pose_error, mat_err(actual, intended))
                if name in prior_pose:
                    max_step = max(max_step, quat_angle(prior_pose[name], actual))
                prior_pose[name] = actual.copy()
            for side, groups in (("left", ("bone_9", "bone_10", "bone_11")),
                                 ("right", ("bone_15", "bone_16", "bone_17"))):
                points = []
                for obj in target_meshes:
                    weights = mesh_group_weights(obj)
                    vertices = evaluated_world_vertices(obj)
                    points.extend(p for p, row in zip(vertices, weights)
                                  if any(row.get(group, 0.0) > 0.01 for group in groups))
                centroid = sum(points, Vector()) / len(points) if points else Vector()
                foot_surface_samples[side].append({"frame": frame,
                    "min_z_world": min((p.z for p in points), default=None),
                    "centroid_world": list(centroid), "vertex_count": len(points)})
            for side, palm, tips in (("left", "bone_30", ("bone_33", "bone_37", "bone_41", "bone_45", "bone_49")),
                                     ("right", "bone_65", ("bone_68", "bone_72", "bone_76", "bone_80", "bone_84"))):
                centers = weighted_mesh_group_centroids(target_meshes, (palm, *tips))
                palm_point = centers[palm]
                tip_points = [centers[name] for name in tips]
                score = sum((point - palm_point).length for point in tip_points if point is not None) / \
                    max(1, sum(point is not None for point in tip_points))
                fist_shape_samples[side].append({"frame": frame, "tip_to_palm_mean_world": score})
        for frame in frames:
            scene.frame_set(int(frame), subframe=frame - int(frame))
            bpy.context.view_layer.update()
        foot_contact_summary = {}
        for side, interval in (("left", (29.0, 34.0)), ("right", (0.0, 3.0))):
            foot_samples = foot_surface_samples[side]
            stance_rows = [r for r in foot_samples if interval[0] <= r["frame"] <= interval[1]]
            if side == "right":
                stance_rows += [r for r in foot_samples if 59.0 <= r["frame"] <= 60.0]
            centroids = [Vector(r["centroid_world"]) for r in stance_rows]
            slides = []
            for lo, hi in ([(29.0, 34.0)] if side == "left" else [(0.0, 3.0), (59.0, 60.0)]):
                segment_centers = [Vector(r["centroid_world"]) for r in foot_samples
                                   if lo <= r["frame"] <= hi]
                slides.extend((p - segment_centers[0]).length for p in segment_centers)
            slide = max(slides, default=0.0)
            heights = [r["min_z_world"] for r in stance_rows if r["min_z_world"] is not None]
            segments = [(interval[0], interval[1])] if side == "left" else [(0.0, 3.0), (59.0, 60.0)]
            ankle_slide = 0.0
            for lo, hi in segments:
                segment_points = [Vector(r["ankle_world"]) for r in foot_rows[side]
                                  if lo <= r["frame"] <= hi]
                if segment_points:
                    ankle_slide = max(ankle_slide,
                        max((p - segment_points[0]).length for p in segment_points))
            foot_contact_summary[side] = {
                "stance_frame_ranges": [list(interval)] if side == "left" else [[0.0, 3.0], [59.0, 60.0]],
                "weighted_surface_slide_max_world": slide,
                "weighted_surface_clearance_range_world": [min(heights), max(heights)] if heights else None,
                "all_frame_surface_clearance_range_world": [
                    min(r["min_z_world"] for r in foot_samples if r["min_z_world"] is not None),
                    max(r["min_z_world"] for r in foot_samples if r["min_z_world"] is not None)],
                "ankle_anchor_slide_max_world": ankle_slide,
            }
        fist_summary = {}
        for side in ("left", "right"):
            scores = [r["tip_to_palm_mean_world"] for r in fist_shape_samples[side]]
            fist_summary[side] = {"baseline_mean_world": baseline_fist_scores[side],
                "fist_mean_world": sum(scores) / len(scores) if scores else 0.0,
                "fist_range_world": scalar_range(scores),
                "reduction_ratio": (sum(scores) / len(scores) / baseline_fist_scores[side]
                    if scores and baseline_fist_scores[side] > 1e-9 else None)}
        max_glove_gap = max((max(v) for v in glove_gaps.values()), default=0.0)
        max_step = max(max_step, max(jumps.values(), default=0.0))
        finite = all(math.isfinite(v) for v in [bake_pose_error, max_step,
            max_glove_gap, baseline_other_errors] + list(jumps.values()))
        runtime_constraints = sum(len(pb.constraints) for pb in target.pose.bones)
        scale_error = max(abs(float(a) - float(b)) for a, b in
            zip(target.matrix_basis.to_scale(), target_base.matrix_basis.to_scale()))
        fist_unchanged = max(baseline_fingers.values(), default=0.0) < 1e-6
        mesh_continuity = {}
        max_mesh_seam_delta = 0.0
        worst_mesh_seam = None
        max_mesh_edge_relative_delta = 0.0
        worst_mesh_edge = None
        trajectory_summary = {}
        for side, rows_for_side in trajectory_rows.items():
            source_flex = [r["source_elbow_flexion_radians"] for r in rows_for_side]
            target_flex = [r["target_elbow_flexion_radians"] for r in rows_for_side]
            source_forward = [r["source_forward_backward"] for r in rows_for_side]
            target_forward = [r["target_forward_backward"] for r in rows_for_side]
            source_lateral = [r["source_lateral_reach_normalized"] for r in rows_for_side]
            target_lateral = [r["target_lateral_reach_normalized"] for r in rows_for_side]
            source_roll = [r["source_forearm_roll_radians"] for r in rows_for_side]
            target_roll = [r["target_forearm_roll_radians"] for r in rows_for_side]
            source_roll_delta = [v - source_roll[0] for v in source_roll]
            target_roll_delta = [v - target_roll[0] for v in target_roll]
            source_shoulder_forward = [r["source_shoulder_excursion_normalized"][2]
                                       for r in rows_for_side]
            target_shoulder_forward = [r["target_shoulder_excursion_normalized"][2]
                                       for r in rows_for_side]
            trajectory_summary[side] = {
                "source_elbow_flexion_range_radians": scalar_range(source_flex),
                "target_elbow_flexion_range_radians": scalar_range(target_flex),
                "elbow_flexion_rmse_radians": rms([r["elbow_flexion_error_radians"]
                                                    for r in rows_for_side]),
                "elbow_flexion_excursion": motion_retention(source_flex, target_flex),
                "forward_backward_source_range_normalized": scalar_range(source_forward),
                "forward_backward_target_range_normalized": scalar_range(target_forward),
                "forward_backward_rmse_normalized": rms([
                    r["source_forward_backward"] - r["target_forward_backward"]
                    for r in rows_for_side]),
                "forward_backward_excursion": motion_retention(source_forward, target_forward),
                "lateral_source_range_normalized": scalar_range(source_lateral),
                "lateral_target_range_normalized": scalar_range(target_lateral),
                "lateral_excursion_retention": motion_retention(source_lateral, target_lateral),
                "shoulder_forward_backward_excursion": motion_retention(
                    source_shoulder_forward, target_shoulder_forward),
                "normalized_shoulder_trajectory_rmse": rms([
                    r["normalized_shoulder_error"] for r in rows_for_side]),
                "normalized_elbow_trajectory_rmse": rms([
                    r["normalized_elbow_trajectory_error"] for r in rows_for_side]),
                "normalized_wrist_trajectory_rmse": rms([
                    r["normalized_wrist_trajectory_error"] for r in rows_for_side]),
                "arm_plane_orientation_rmse_radians": rms([
                    r["arm_plane_orientation_error_radians"] for r in rows_for_side]),
                "source_forearm_roll_range_radians": scalar_range([
                    r["source_forearm_roll_radians"] for r in rows_for_side]),
                "target_forearm_roll_range_radians": scalar_range([
                    r["target_forearm_roll_radians"] for r in rows_for_side]),
                "forearm_roll_motion_rmse_radians": rms([
                    s - t for s, t in zip(source_roll_delta, target_roll_delta)]),
                "forearm_roll_excursion": motion_retention(source_roll_delta,
                                                            target_roll_delta),
                "forearm_roll_absolute_rest_offset_rmse_radians": rms([
                    r["forearm_roll_error_radians"] for r in rows_for_side]),
                "wrist_relative_orientation_rmse_radians": rms([
                    r["wrist_relative_orientation_error_radians"] for r in rows_for_side]),
                "frames": rows_for_side,
            }
        for frame, pair in mesh_gaps_by_frame.items():
            delta, worst, edge_delta, edge_worst = max_seam_delta(
                pair["candidate"], pair["baseline"])
            mesh_continuity[frame] = {"max_surface_gap_delta": delta,
                                      "worst_surface_gap": worst,
                                      "max_cross_weight_edge_p95_relative_delta": edge_delta,
                                      "worst_cross_weight_edge": edge_worst}
            if delta > max_mesh_seam_delta:
                max_mesh_seam_delta = delta
                worst_mesh_seam = {"frame": int(float(frame)), **worst}
            if edge_delta > max_mesh_edge_relative_delta:
                max_mesh_edge_relative_delta = edge_delta
                worst_mesh_edge = {"frame": int(float(frame)), **edge_worst}
        armature_world_scale = max(abs(float(s)) for s in target.matrix_basis.to_scale())
        mesh_gap_tolerance_world = args.max_glove_gap_delta * armature_world_scale
        trajectory_gates = {}
        for side, summary in trajectory_summary.items():
            flex_retention = summary["elbow_flexion_excursion"]["retention_ratio"]
            forward_retention = summary["forward_backward_excursion"]["retention_ratio"]
            roll_retention = summary["forearm_roll_excursion"]["retention_ratio"]
            trajectory_gates[f"{side}_elbow_flexion"] = (
                summary["elbow_flexion_rmse_radians"] < 0.03 and
                flex_retention is not None and 0.80 <= flex_retention <= 1.20)
            trajectory_gates[f"{side}_forward_backward_swing"] = (
                summary["forward_backward_rmse_normalized"] < 0.15 and
                forward_retention is not None and 0.80 <= forward_retention <= 1.25)
            lateral_retention = summary["lateral_excursion_retention"]["retention_ratio"]
            trajectory_gates[f"{side}_lateral_compression"] = (
                lateral_retention is not None and 0.70 <= lateral_retention <= 0.92)
            trajectory_gates[f"{side}_arm_plane_and_roll"] = (
                summary["arm_plane_orientation_rmse_radians"] < 1.25 and
                summary["forearm_roll_motion_rmse_radians"] < 0.15 and
                roll_retention is not None and 0.80 <= roll_retention <= 1.20 and
                summary["wrist_relative_orientation_rmse_radians"] < 1e-3)
        numerical_gates = {
            "finite": finite,
            "target_scale_unchanged": scale_error < 1e-6,
            "non_arm_local_channels_unchanged": baseline_other_errors < 1e-6,
            "closed_fist_channels_unchanged": fist_unchanged,
            "no_runtime_constraints": runtime_constraints == 0,
            "bake_replay_error": bake_pose_error < 1e-5,
            "pose_jump": max_step < args.max_jump_radians,
            "mesh_space_elbow_wrist_continuity": max_mesh_seam_delta < mesh_gap_tolerance_world,
            "mesh_space_cross_weight_edges": max_mesh_edge_relative_delta < 0.20,
            "left_stance_foot_contact": (
                foot_contact_summary["left"]["ankle_anchor_slide_max_world"] < 0.5 and
                foot_contact_summary["left"]["weighted_surface_slide_max_world"] < 0.5 and
                foot_contact_summary["left"]["weighted_surface_clearance_range_world"] is not None and
                -0.5 <= foot_contact_summary["left"]["weighted_surface_clearance_range_world"][0] and
                foot_contact_summary["left"]["weighted_surface_clearance_range_world"][1] <= 1.0),
            "right_stance_foot_contact": (
                foot_contact_summary["right"]["ankle_anchor_slide_max_world"] < 0.5 and
                foot_contact_summary["right"]["weighted_surface_slide_max_world"] < 0.5 and
                foot_contact_summary["right"]["weighted_surface_clearance_range_world"] is not None and
                -0.5 <= foot_contact_summary["right"]["weighted_surface_clearance_range_world"][0] and
                foot_contact_summary["right"]["weighted_surface_clearance_range_world"][1] <= 1.0),
            "left_and_right_closed_fist_geometry": all(
                fist_summary[s]["reduction_ratio"] is not None and
                fist_summary[s]["reduction_ratio"] < 0.95 and
                fist_summary[s]["fist_range_world"][1] - fist_summary[s]["fist_range_world"][0] < 1.0
                for s in ("left", "right")),
        }
        numerical_gates.update(trajectory_gates)
        passed = all(numerical_gates.values())
        report = {
            "schema": "mg1.direct-galaxy-walk-adapter-probe.v1",
            "input_blend": bpy.data.filepath,
            "source_rig": source.name,
            "source_action": source_action.name,
            "source_action_slot": source_slot.identifier,
            "source_action_curve_count": len(source_curves),
            "source_action_evaluation": "selected-slot FCurve evaluation at quarter-frame samples with shortest-quaternion correction for wrapped Euler intervals",
            "source_channel_diagnostics": source_channel_samples,
            "source_and_target_world_joint_trajectories": trajectory_world_samples,
            "anatomical_trajectory_validation": trajectory_summary,
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
            "output_action": target_action.name,
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
            "foot_contact": foot_contact_summary,
            "foot_surface_samples": foot_surface_samples,
            "native_fist_geometry": fist_summary,
            "native_fist_candidate_scores_world": native_fist_candidates,
            "native_fist_selected_sources": fist_pose_sources,
            "mesh_space_seam_measurements": mesh_gaps_by_frame,
            "mesh_space_continuity_summary": {
                "max_surface_gap_delta_vs_walk_baseline_world_units": max_mesh_seam_delta,
                "worst_surface_gap": worst_mesh_seam,
                "max_cross_weight_edge_p95_relative_delta": max_mesh_edge_relative_delta,
                "worst_cross_weight_edge": worst_mesh_edge,
                "surface_gap_tolerance_world_units": mesh_gap_tolerance_world,
                "surface_gap_tolerance_armature_units": args.max_glove_gap_delta,
                "per_frame": mesh_continuity,
            },
            "baseline_non_arm_basis_error": baseline_other_errors,
            "fist_basis_error": baseline_fingers,
            "bake_replay_matrix_error": bake_pose_error,
            "runtime_constraints": runtime_constraints,
            "scale_error": scale_error,
            "gates": numerical_gates,
            "passed": passed,
            "preview": os.path.abspath(args.preview) if passed else None,
            "limitations": [
                "This control chain retargets only the Luigi walk, not a complete move set.",
                "The source action has no independently authored finger channels; target fists remain at baseline.",
                "Mesh seam checks use actual armature-evaluated vertices and nearest surfaces; they do not replace visual review.",
            ],
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, allow_nan=False)
            f.write("\n")
        if passed:
            # The baseline geometry probe is disposable and must not be visible
            # or saved in the sign-off file.
            for obj in baseline_meshes:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:
                    bpy.data.meshes.remove(data)
            rig_data = baseline_probe.data
            bpy.data.objects.remove(baseline_probe, do_unlink=True)
            if rig_data.users == 0:
                bpy.data.armatures.remove(rig_data)
            visible = {target, *target_meshes}
            hide_reference_objects(visible)
            visible_armatures = [o.name for o in bpy.data.objects
                                 if o.type == 'ARMATURE' and not o.hide_get() and not o.hide_viewport]
            visible_meshes = [o.name for o in bpy.data.objects
                              if o.type == 'MESH' and not o.hide_get() and not o.hide_viewport]
            if visible_armatures != [target.name] or set(visible_meshes) != set(target_mesh_names):
                raise RuntimeError("preview visibility validation found duplicate/hidden character geometry: "
                                   f"armatures={visible_armatures}, meshes={visible_meshes}, "
                                   f"expected_meshes={target_mesh_names}")
            for obj in target_meshes:
                if not any(m.type == 'ARMATURE' and m.object == target for m in obj.modifiers):
                    raise RuntimeError(f"preview mesh lost its target armature binding: {obj.name}")
            scene.frame_set(0)
            bpy.context.view_layer.update()
            # Save to a new path only; never save over the source workfile.
            bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
        else:
            print("Direct walk prototype rejected by numerical gates; preview not saved.")
        print(json.dumps({"report": os.path.abspath(args.report), "passed": passed,
                          "gates": numerical_gates,
                          "max_step": max_step, "max_mesh_gap_delta": max_mesh_seam_delta,
                          "worst_mesh_seam": worst_mesh_seam}))
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
        bpy.context.view_layer.update()


if __name__ == "__main__":
    main()
