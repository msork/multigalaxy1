"""Blender-background tests for direct rest-basis transform transfer."""
import math
import os
import sys
import unittest

from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from literal_retarget_math import (local_frame_conversion, map_armature_pose_rotation,
                                   map_local_basis, quaternion_angle)


class LiteralRetargetMathTests(unittest.TestCase):
    def test_identity_rest_frames_preserve_local_rotation_and_translation(self):
        q = Quaternion(Vector((0.2, 0.7, -0.1)).normalized(), 0.63)
        source = Matrix.LocRotScale(Vector((0.2, -0.4, 0.1)), q, (1, 1, 1))
        identity = Matrix.Identity(4)
        conversion = local_frame_conversion(identity, identity, identity, identity)
        actual = map_local_basis(source, conversion, 1.0)
        self.assertLess(quaternion_angle(actual.to_quaternion(), q), 1e-6)
        self.assertLess((actual.translation - source.translation).length, 1e-6)

    def test_rest_frame_conversion_changes_axes_without_changing_angle(self):
        identity = Matrix.Identity(4)
        src_rest = Matrix.Rotation(math.pi / 2, 4, "X")
        dst_rest = Matrix.Rotation(-math.pi / 2, 4, "Y")
        conversion = local_frame_conversion(identity, src_rest, identity, dst_rest)
        source_delta = Quaternion(Vector((0, 0, 1)), 0.47)
        basis = Matrix.LocRotScale(Vector((0, 0, 0)), source_delta, (1, 1, 1))
        mapped = map_local_basis(basis, conversion, 1.0)
        self.assertAlmostEqual(quaternion_angle(mapped.to_quaternion(), Quaternion()), 0.47, places=5)
        expected = (conversion @ source_delta @ conversion.inverted()).normalized()
        self.assertLess(quaternion_angle(mapped.to_quaternion(), expected), 1e-6)

    def test_translation_uses_only_declared_proportional_ratio(self):
        identity = Matrix.Identity(4)
        basis = Matrix.Translation((1.0, -2.0, 3.0)) @ Matrix.Rotation(0.2, 4, "Z")
        mapped = map_local_basis(basis, Quaternion(), 0.25)
        self.assertLess((mapped.translation - Vector((0.25, -0.5, 0.75))).length, 1e-6)
        self.assertEqual(tuple(round(float(x), 6) for x in mapped.to_scale()), (1.0, 1.0, 1.0))

    def test_pose_rest_delta_applies_at_target_rest_orientation(self):
        source_object = Matrix.Rotation(0.3, 4, "Z")
        target_object = Matrix.Rotation(-0.2, 4, "X")
        source_rest = Matrix.Rotation(0.6, 4, "Y")
        target_rest = Matrix.Rotation(-0.8, 4, "Z")
        source_delta = Quaternion(Vector((0.2, 0.3, 1.0)).normalized(), 0.52)
        source_pose = Matrix.Rotation(source_delta.angle, 4, source_delta.axis) @ source_rest
        actual = map_armature_pose_rotation(source_pose, source_rest, source_object,
                                            target_rest, target_object)
        object_map = (target_object.to_quaternion().inverted() @
                      source_object.to_quaternion())
        expected = (object_map @ source_delta @ object_map.inverted() @
                    target_rest.to_quaternion()).normalized()
        self.assertLess(quaternion_angle(actual, expected), 1e-6)

    def test_zero_source_pose_delta_returns_target_rest_pose(self):
        source_object = Matrix.Rotation(0.9, 4, "X")
        target_object = Matrix.Rotation(-0.5, 4, "Y")
        source_rest = Matrix.Rotation(0.1, 4, "Z")
        target_rest = Matrix.Rotation(1.2, 4, "X")
        actual = map_armature_pose_rotation(source_rest, source_rest, source_object,
                                            target_rest, target_object)
        self.assertLess(quaternion_angle(actual, target_rest.to_quaternion()), 1e-6)

    def test_quaternion_sign_equivalence(self):
        q = Quaternion(Vector((0.4, 1, 0.2)).normalized(), 0.9)
        self.assertLess(quaternion_angle(q, -q), 1e-6)


if __name__ == "__main__":
    sys.argv = [__file__]
    unittest.main(verbosity=2)
