"""Small deterministic tests for the semantic feature/sequence objective."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from semantic_arm_fit_math import (
    farthest_centers, predict_rbf, fit_rbf_ridge, semantic_features,
    smooth_latent_sequence, mirror_pose_across_sagittal,
    refine_latent_sequence_joint,
)
from native_pose_graph import pose_pair_distance, solve_native_pose_path


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

    def test_joint_sequence_solver_reduces_semantic_step_without_post_smoothing(self):
        target = np.zeros((9, 1))
        target[4:, 0] = 1.0
        initial = target.copy()
        prior = np.zeros_like(initial)
        solved, values, history = refine_latent_sequence_joint(
            initial, prior, lambda z: z.copy(), target, np.ones(1),
            temporal_specs=[{"index": 0, "velocity_target": np.zeros(8),
                "acceleration_target": np.zeros(7), "velocity_weight": 20.0,
                "acceleration_weight": 5.0}],
            lambda_native=0.01, lambda_velocity=0.01,
            lambda_acceleration=0.01, iterations=8)
        self.assertLess(np.max(np.abs(np.diff(values[:, 0]))), 0.8)
        self.assertLess(history[-1]["objective"],
                        0.5 * float(np.sum(target * target)))
        self.assertTrue(np.isfinite(solved).all())

    def test_joint_temporal_block_reduces_full_pose_context_jumps(self):
        target = np.zeros((9, 3))
        target[4:, 0] = 1.0
        initial = np.column_stack((target[:, 0], target[:, 0] * -0.5,
                                   np.zeros(len(target))))
        prior = np.zeros_like(initial)
        indices = np.asarray([1, 2])
        solved, values, _history = refine_latent_sequence_joint(
            initial, prior, lambda z: z.copy(), target, np.asarray([1.0, 0.0, 0.0]),
            temporal_blocks=[{"indices": indices,
                "velocity_target": np.zeros((8, 2)),
                "acceleration_target": np.zeros((7, 2)),
                "velocity_weights": 2.0, "acceleration_weights": 8.0}],
            lambda_native=0.0, lambda_velocity=0.0,
            lambda_acceleration=0.0, iterations=6)
        self.assertLess(np.max(np.linalg.norm(np.diff(values[:, 1:], axis=0), axis=1)), 0.3)
        self.assertTrue(np.isfinite(solved).all())

    def test_latent_neighborhood_hinge_limits_distant_switches(self):
        initial = np.zeros((7, 3))
        initial[3:, :] = (3.0, -2.0, 1.0)
        prior = np.zeros_like(initial)
        solved, _values, _history = refine_latent_sequence_joint(
            initial, prior, lambda z: z.copy(), initial.copy(), np.zeros(3),
            lambda_native=0.0, lambda_velocity=0.0,
            lambda_acceleration=0.0, iterations=12,
            latent_jump_limit=0.2, lambda_latent_jump=80.0)
        steps = np.linalg.norm(np.diff(solved, axis=0), axis=1)
        self.assertLess(float(np.max(steps)), 0.45)
        self.assertTrue(np.isfinite(solved).all())

    def test_pose_velocity_hinge_limits_large_context_steps(self):
        target = np.zeros((8, 2))
        target[4:, 0] = 2.0
        solved, values, _history = refine_latent_sequence_joint(
            target.copy(), np.zeros_like(target), lambda z: z.copy(), target,
            np.asarray([1.0, 0.0]), temporal_blocks=[{
                "indices": np.asarray([0, 1]),
                "velocity_target": np.zeros((7, 2)),
                "acceleration_target": np.zeros((6, 2)),
                "velocity_weights": 0.0,
                "acceleration_weights": 0.0,
                "velocity_limits": 0.2,
                "velocity_limit_weights": 80.0}],
            lambda_native=0.0, lambda_velocity=0.0,
            lambda_acceleration=0.0, iterations=12)
        self.assertLess(float(np.max(np.abs(np.diff(values[:, 0])))), 0.3)
        self.assertTrue(np.isfinite(solved).all())

    def test_joint_evaluator_receives_frame_index_for_composed_context(self):
        initial = np.asarray([[1.0], [2.0], [3.0]])
        prior = np.zeros_like(initial)
        solved, values, _history = refine_latent_sequence_joint(
            initial, prior, lambda row, frame: row + frame,
            np.zeros((3, 1)), np.zeros(1), iterations=0,
            evaluate_with_index=True)
        np.testing.assert_allclose(solved, initial)
        np.testing.assert_allclose(values[:, 0], (1.0, 3.0, 5.0))

    def test_native_pose_graph_uses_quaternion_sign_equivalence(self):
        first = np.asarray([[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]])
        opposite = first.copy()
        opposite[:, 3:] *= -1.0
        maximum, rms, per_bone = pose_pair_distance(first, opposite)
        self.assertAlmostEqual(maximum, 0.0)
        self.assertAlmostEqual(rms, 0.0)
        self.assertAlmostEqual(float(per_bone[0]), 0.0)

    def test_native_path_prefers_temporal_edges_and_obeys_jump_limit(self):
        ids = [[0, 1], [1, 2], [2, 3]]
        sem = [np.asarray([[0.0], [0.1]]),
               np.asarray([[0.1], [0.2]]),
               np.asarray([[0.2], [0.3]])]
        descriptors = []
        for row in ids:
            descriptors.append(np.asarray([
                [[float(node) * 0.05, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]]
                for node in row]))
        graph = {"temporal_next": {0: 1, 1: 2, 2: 3},
                 "semantic_neighbors": [(1,), (0, 2), (1, 3), (2,)]}
        result = solve_native_pose_path(
            ids, [np.zeros(2) for _ in ids], sem, descriptors,
            np.asarray([[0.0], [0.1], [0.2]]),
            ["a", "a", "a", "a"], [0.0, 0.5, 1.0, 1.5], graph,
            max_pose_jump=0.5)
        self.assertEqual(result["node_ids"], [0, 1, 2])
        self.assertEqual(result["temporal_transition_count"], 2)
        self.assertEqual(result["action_switch_count"], 0)
        self.assertLess(result["maximum_transition_jump"], 0.5)

    def test_native_path_rejects_reverse_time_neighbor_edges(self):
        pose = np.asarray([[[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]],
                           [[0.01, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]]])
        graph = {"temporal_next": {0: 1}, "semantic_neighbors": [(1,), (0,)]}
        with self.assertRaisesRegex(RuntimeError, "no admissible native-pose path"):
            solve_native_pose_path(
                [[0], [1], [0]], [np.zeros(1)] * 3,
                [np.zeros((1, 1))] * 3, [pose[[0]], pose[[1]], pose[[0]]],
                np.zeros((3, 1)), ["same", "same"], [0.0, 1.0], graph,
                max_pose_jump=0.5)

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
