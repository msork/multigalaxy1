import math
import unittest

from rotation_math import angular_distance, pearson, relative


class RotationMathTests(unittest.TestCase):
    def test_quaternion_sign_equivalence(self):
        q = (math.cos(0.3), math.sin(0.3), 0.0, 0.0)
        self.assertAlmostEqual(angular_distance(q, tuple(-x for x in q)), 0.0, places=8)

    def test_angular_distance_normalizes_inputs(self):
        identity = (3.0, 0.0, 0.0, 0.0)
        half_turn = (0.0, 2.0, 0.0, 0.0)
        self.assertAlmostEqual(math.degrees(angular_distance(identity, half_turn)), 180.0, places=6)

    def test_relative_orientation_is_invariant_to_quaternion_sign(self):
        a = (math.cos(0.2), 0.0, math.sin(0.2), 0.0)
        b = (math.cos(0.7), math.sin(0.7), 0.0, 0.0)
        self.assertAlmostEqual(angular_distance(relative(a, b), relative(tuple(-x for x in a), b)), 0.0, places=8)

    def test_pearson_uses_displacement_sequences(self):
        self.assertAlmostEqual(pearson([0, 1, 2], [2, 4, 6]), 1.0)
        self.assertIsNone(pearson([1, 1], [2, 3]))


if __name__ == "__main__":
    unittest.main()
