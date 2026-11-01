"""Run with Blender's bundled Python: blender --background --python this_file.py"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_native_arm_pose_model as model


class PoseModelMathTests(unittest.TestCase):
    def test_quaternion_sign_and_full_turn_are_equivalent(self):
        self.assertAlmostEqual(model.quat_angle(np.zeros(3), np.asarray([0.0, 0.0, 2.0 * np.pi])), 0.0, places=7)

    def test_feature_encoding_is_sign_invariant(self):
        def sample(q):
            return {"bones": {"b": {"rest_relative_se3": {
                "scale": [1, 1, 1], "translation": [1, 2, 3],
                "rotation_quaternion": q}}}}
        a = model.encode_sample(sample([0.8, 0.2, -0.3, 0.4]), ["b"])
        b = model.encode_sample(sample([-0.8, -0.2, 0.3, -0.4]), ["b"])
        np.testing.assert_allclose(a, b, atol=1e-12)

    def test_pca_reconstructs_full_rank_training_space(self):
        rng = np.random.default_rng(23)
        x = rng.normal(size=(64, 6))
        fitted, _ = model.fit_pca(x, 6)
        np.testing.assert_allclose(model.pca_predict(fitted, x), x, atol=1e-12)

    def test_rbf_decoder_returns_finite_full_features(self):
        rng = np.random.default_rng(11)
        z = rng.normal(size=(80, 4))
        y = np.column_stack((z, z[:, :1] ** 2))
        fitted = model.fit_rbf_decoder(z, y, 24, 1.0, 1e-3)
        output = model.rbf_predict(fitted, z[:7])
        self.assertEqual(output.shape, (7, 5))
        self.assertTrue(np.isfinite(output).all())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
