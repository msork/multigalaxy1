"""Small, reusable rest-space primitives for direct Galaxy rig adapters.

The source action datablock remains read-only. This module provides transform
calibration for sampled source curves and a target adapter; it does not read
Sluggers pose samples or fit a learned model.
"""

from mathutils import Matrix


def object_rotation(obj, fallback_basis=None):
    """Extract the proper rotation from an authored object transform.

    Some saved preview files reopen with a stale identity ``matrix_world`` even
    though the intended transform remains in ``matrix_basis``. Pass the
    authored basis in that case. Scale is intentionally excluded.
    """
    matrix = fallback_basis if fallback_basis is not None else obj.matrix_world
    return matrix.to_3x3().to_quaternion().to_matrix()


def rest_calibrated_rotation(source_pose, source_rest, source_object_rotation,
                             target_rest, target_object_rotation):
    """Map a source absolute pose orientation onto a target rest orientation.

    The result is a target armature-space rotation. Bone head translation and
    scale are deliberately absent, preserving the target's own proportions.
    Quaternion signs are immaterial because this operates on rotation
    matrices.
    """
    source_delta = source_pose.to_3x3() @ source_rest.to_3x3().inverted()
    world_delta = (source_object_rotation @ source_delta @
                   source_object_rotation.inverted())
    target_delta = (target_object_rotation.inverted() @ world_delta @
                    target_object_rotation)
    return target_delta @ target_rest.to_3x3()


def validate_mapping(mapping, source_bones, target_bones):
    """Fail closed on missing source or output bones and duplicate outputs."""
    missing_source = sorted({src for src, _targets in mapping.items()
                             if src not in source_bones})
    outputs = [target for targets in mapping.values() for target in targets]
    missing_target = sorted(set(outputs) - set(target_bones))
    duplicates = sorted(name for name in set(outputs) if outputs.count(name) > 1)
    if missing_source or missing_target or duplicates:
        raise ValueError({"missing_source": missing_source,
                          "missing_target": missing_target,
                          "duplicate_target_outputs": duplicates})
