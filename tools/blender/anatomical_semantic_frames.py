"""Rest-calibrated anatomical frames for cross-character semantic features.

The input rests and endpoints are armature-space. Object transforms are reduced
to proper rotation, so intentional object scale never affects semantic axes.
"""
from __future__ import annotations

import numpy as np


def _rotation(matrix):
    a = np.asarray(matrix, dtype=np.float64)[:3, :3]
    u, _s, vt = np.linalg.svd(a)
    result = u @ vt
    if np.linalg.det(result) < 0.0:
        u[:, -1] *= -1.0
        result = u @ vt
    return result


def _unit(vector):
    vector = np.asarray(vector, dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length < 1e-10:
        raise ValueError("rest geometry cannot define an anatomical axis")
    return vector / length


def _point(rest_bones, object_matrix, bone_name, endpoint):
    point = np.r_[np.asarray(rest_bones[bone_name][endpoint], dtype=np.float64), 1.0]
    return (np.asarray(object_matrix, dtype=np.float64).reshape(4, 4) @ point)[:3]


def derive_anatomical_frames(rest_bones, object_matrix, left_profile,
                             right_profile):
    """Return torso/forearm maps and rest-local twist axes for one rig side.

    The shared body basis is +X character-right, +Y torso-up, +Z their
    right-handed cross product. The forearm basis uses the distal limb axis and
    projected body-forward direction. All returned maps send bone-local
    semantic coordinates into those geometry-derived anatomical coordinates.
    """
    matrix_world = np.asarray(object_matrix, dtype=np.float64).reshape(4, 4)
    object_rotation = _rotation(matrix_world)

    torso = left_profile["torso"]
    torso_head = _point(rest_bones, matrix_world, torso, "head")
    torso_tail = _point(rest_bones, matrix_world, torso, "tail")
    up = _unit(torso_tail - torso_head)
    left_shoulder = _point(rest_bones, matrix_world,
                           left_profile["shoulder_point_bone"],
                           left_profile["shoulder_endpoint"])
    right_shoulder = _point(rest_bones, matrix_world,
                            right_profile["shoulder_point_bone"],
                            right_profile["shoulder_endpoint"])
    lateral = right_shoulder - left_shoulder
    lateral -= up * float(np.dot(lateral, up))
    lateral = _unit(lateral)
    forward = _unit(np.cross(lateral, up))
    body_frame_world = np.column_stack((lateral, up, forward))
    if np.linalg.det(body_frame_world) < 0.999:
        raise ValueError("derived body anatomical frame is not right-handed")
    torso_rest_rotation = np.asarray(
        rest_bones[torso]["rest_matrix"], dtype=np.float64).reshape(4, 4)[:3, :3]
    torso_to_anatomical = body_frame_world.T @ object_rotation @ torso_rest_rotation

    def limb_frame(profile):
        elbow_a = _point(rest_bones, matrix_world, profile["elbow_a"],
                         profile["elbow_endpoint_a"])
        elbow_b = _point(rest_bones, matrix_world, profile["elbow_b"],
                         profile["elbow_endpoint_b"])
        elbow = 0.5 * (elbow_a + elbow_b)
        wrist = _point(rest_bones, matrix_world, profile["wrist"],
                        profile["wrist_endpoint"])
        distal = _unit(wrist - elbow)
        forearm_forward = body_frame_world[:, 2] - distal * float(
            np.dot(body_frame_world[:, 2], distal))
        if np.linalg.norm(forearm_forward) < 1e-8:
            forearm_forward = body_frame_world[:, 1] - distal * float(
                np.dot(body_frame_world[:, 1], distal))
        forearm_forward = _unit(forearm_forward)
        forearm_lateral = _unit(np.cross(distal, forearm_forward))
        forearm_forward = _unit(np.cross(forearm_lateral, distal))
        forearm_frame_world = np.column_stack((forearm_lateral, distal,
                                               forearm_forward))
        if np.linalg.det(forearm_frame_world) < 0.999:
            raise ValueError("derived forearm anatomical frame is not right-handed")
        forearm_name = profile["forearm"]
        overlap_name = profile["forearm_overlap"]
        forearm_rotation = np.asarray(
            rest_bones[forearm_name]["rest_matrix"], dtype=np.float64).reshape(4, 4)[:3, :3]
        overlap_rotation = np.asarray(
            rest_bones[overlap_name]["rest_matrix"], dtype=np.float64).reshape(4, 4)[:3, :3]
        distal_armature = object_rotation.T @ distal
        axes = {
            forearm_name: _unit(forearm_rotation.T @ distal_armature),
            overlap_name: _unit(overlap_rotation.T @ distal_armature),
        }
        forearm_to_anatomical = forearm_frame_world.T @ object_rotation @ forearm_rotation
        return forearm_to_anatomical, axes, {
            "distal_axis_world": distal.tolist(),
            "body_forward_world": body_frame_world[:, 2].tolist(),
            "forearm_frame_world": forearm_frame_world.tolist(),
        }

    sides = {"left": limb_frame(left_profile),
             "right": limb_frame(right_profile)}
    forearm_to_anatomical, axes, diagnostics = sides["left"]
    return {
        "body_frame_world": body_frame_world,
        "torso_to_anatomical": torso_to_anatomical,
        "forearm_to_anatomical": forearm_to_anatomical,
        "forearm_axes_local": axes,
        "diagnostics": diagnostics,
        "sides": {name: {"forearm_to_anatomical": values[0],
                         "forearm_axes_local": values[1],
                         "diagnostics": values[2]}
                  for name, values in sides.items()},
    }
