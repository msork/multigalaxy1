import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from native_scaffold_sequence import solve_scaffold_sequence


class NativeScaffoldSequenceTests(unittest.TestCase):
    def test_exact_timed_scaffold_is_preserved(self):
        semantics = np.arange(5, dtype=np.float64)[:, None]
        poses = np.zeros((5, 2, 7), dtype=np.float64)
        poses[:, :, 3] = 1.0
        result = solve_scaffold_sequence(semantics, semantics, poses,
                                        phase_radius=2)
        self.assertEqual(result["sample_indices"], [0, 1, 2, 3, 4])

    def test_time_mapping_is_monotone_and_uses_only_scaffold_samples(self):
        source = np.linspace(0.0, 1.0, 9)[:, None]
        target = np.linspace(0.0, 1.0, 5)[:, None]
        poses = np.zeros((5, 1, 7), dtype=np.float64)
        poses[:, :, 3] = 1.0
        result = solve_scaffold_sequence(source, target, poses, phase_radius=3)
        indices = result["sample_indices"]
        self.assertEqual(len(indices), len(source))
        self.assertTrue(all(0 <= value < len(target) for value in indices))
        self.assertTrue(all(a <= b and b - a <= 2
                            for a, b in zip(indices, indices[1:])))
        self.assertTrue(all(row["max_pose_jump"] < 1e-9
                            for row in result["transitions"]))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
