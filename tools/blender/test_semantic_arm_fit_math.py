"""Small deterministic tests for the semantic feature/sequence objective."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from semantic_arm_fit_math import (
    farthest_centers, predict_rbf, fit_rbf_ridge, semantic_features,
    smooth_latent_sequence, mirror_pose_across_sagittal,
)


def rest_matrix(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


class SemanticArmFitMathTests(unittest.TestCase):
    def test_rest_pose_has_zero_semantics(self):
        rest = {
            "torso": rest_matrix(0, 0, 0),
            "shoulder": rest_matrix(0, 1, 0),
            "upper": rest_matrix(0, 1, 0),
            "elbow_a": rest_matrix(1, 1, 0),
            "elbow_b": rest_matrix(1, 1, 0),
            "forearm": rest_matrix(1, 1, 0),
            "overlap": rest_matrix(1, 1, 0),
            "wrist": rest_matrix(2, 1, 0),
            "hand": rest_matrix(2, 1, 0),
        }
        rest_bones = {name: {"rest_matrix": matrix.tolist(),
                             "head": matrix[:3, 3].tolist(),
                             "tail": (matrix[:3, 3] + [0, 0.25, 0]).tolist()}
                      for name, matrix in rest.items()}
        profile = {"torso": "torso", "shoulder": "shoulder", "upper": "upper",
            "elbow_a": "elbow_a", "elbow_b": "elbow_b", "forearm": "forearm",
            "forearm_overlap": "overlap", "wrist": "wrist", "hand": "hand",
            "shoulder_point_bone": "upper", "shoulder_endpoint": "head",
            "elbow_endpoint_a": "tail", "elbow_endpoint_b": "head",
            "forearm_endpoint": "tail", "overlap_endpoint": "tail",
            "wrist_endpoint": "head", "hand_endpoint": "tail"}
        pose = {name: matrix.tolist() for name, matrix in rest.items()}
        features = semantic_features(rest_bones, pose, profile)
        np.testing.assert_allclose(features, np.zeros(11), atol=1e-10)

    def test_rbf_fit_and_prediction_are_finite(self):
        rng = np.random.default_rng(41)
        x = rng.normal(size=(120, 4))
        y = np.column_stack((np.sin(x[:, 0]), x[:, 1] ** 2))
        centers = farthest_centers(x, 24)
        model = fit_rbf_ridge(x, y, centers, gamma=0.5, alpha=1e-3)
        predicted = predict_rbf(x[:12], model)
        self.assertEqual(predicted.shape, (12, 2))
        self.assertTrue(np.isfinite(predicted).all())

    def test_sequence_smoothing_reduces_alternating_latent_motion(self):
        proposal = np.zeros((21, 3))
        proposal[1::2, 0] = 1.0
        prior = np.zeros_like(proposal)
        smooth = smooth_latent_sequence(proposal, prior, np.ones(3),
                                       lambda_native=0.1,
                                       lambda_velocity=0.1,
                                       lambda_acceleration=2.0)
        self.assertLess(np.mean(np.diff(smooth[:, 0]) ** 2),
                        np.mean(np.diff(proposal[:, 0]) ** 2))
        self.assertTrue(np.isfinite(smooth).all())

    def test_sagittal_mirror_maps_rest_pose_to_homologous_rest(self):
        torso = rest_matrix(0.0, 0.0, 0.0)
        left = rest_matrix(1.0, 2.0, 0.25)
        right = rest_matrix(-1.0, 2.0, 0.25)
        actual = mirror_pose_across_sagittal(left, torso, torso, left, right)
        np.testing.assert_allclose(actual, right, atol=1e-12)

    def test_sagittal_mirror_preserves_proper_pose_rotation(self):
        torso = rest_matrix(0.0, 0.0, 0.0)
        left = rest_matrix(1.0, 2.0, 0.25)
        right = rest_matrix(-1.0, 2.0, 0.25)
        moved = left.copy()
        angle = 0.4
        moved[:3, :3] = np.array([[np.cos(angle), -np.sin(angle), 0.0],
                                  [np.sin(angle), np.cos(angle), 0.0],
                                  [0.0, 0.0, 1.0]])
        actual = mirror_pose_across_sagittal(moved, torso, torso, left, right)
        self.assertAlmostEqual(np.linalg.det(actual[:3, :3]), 1.0, places=12)
        self.assertGreater(np.linalg.norm(actual[:3, :3] - np.eye(3)), 0.1)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
