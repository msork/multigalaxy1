"""Small character-neutral transforms for literal rest-space retargeting."""
from mathutils import Matrix, Quaternion, Vector


def rotation_of(matrix):
    return matrix.to_3x3().to_quaternion().normalized()


def local_frame_conversion(source_object, source_rest, target_object, target_rest):
    """Map a vector/operator in source bone-local rest coordinates to target.

    The returned constant rotation accounts for each armature object's authored
    orientation and the corresponding bone's rest orientation. Object scale is
    intentionally excluded; translation scale is supplied per mapped bone.
    """
    source_world_rest = rotation_of(source_object) @ rotation_of(source_rest)
    target_world_rest = rotation_of(target_object) @ rotation_of(target_rest)
    result = target_world_rest.inverted() @ source_world_rest
    result.normalize()
    return result


def map_local_basis(source_basis, conversion, translation_ratio):
    """Rest-calibrate a sampled source pose basis without inventing motion."""
    source_rotation = source_basis.to_quaternion().normalized()
    target_rotation = (conversion @ source_rotation @ conversion.inverted()).normalized()
    target_location = conversion @ source_basis.translation * float(translation_ratio)
    target_scale = source_basis.to_scale()
    return Matrix.LocRotScale(target_location, target_rotation, target_scale)


def map_armature_pose_rotation(source_pose, source_rest, source_object,
                               target_rest, target_object):
    """Transfer an evaluated joint orientation through both rest bases.

    The source pose/rest delta is formed in armature space, converted between
    object coordinate frames, then applied at the target rest orientation. This
    supports different parent layouts and lets multiple target branches share
    one source joint without accumulating the same local delta down a chain.
    """
    source_delta = rotation_of(source_pose) @ rotation_of(source_rest).inverted()
    object_map = rotation_of(target_object).inverted() @ rotation_of(source_object)
    target_delta = object_map @ source_delta @ object_map.inverted()
    result = target_delta @ rotation_of(target_rest)
    result.normalize()
    return result


def quaternion_angle(a, b):
    qa, qb = a.copy(), b.copy()
    qa.normalize(); qb.normalize()
    dot = min(1.0, max(-1.0, abs(float(qa.dot(qb)))))
    import math
    return 2.0 * math.acos(dot)
