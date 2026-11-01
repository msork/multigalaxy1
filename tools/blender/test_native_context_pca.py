"""PCA-only math tests; run with Blender's bundled Python."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_native_context_pca as pca


class NativeContextPcaTests(unittest.TestCase):
    def test_rotation_vector_wrap_is_sign_invariant(self):
        self.assertAlmostEqual(pca.rotvec_angle(
            np.asarray([0.3, 0.0, 0.0]),
            np.asarray([0.3 - 2 * np.pi, 0.0, 0.0])), 0.0, places=7)

    def test_full_rank_pca_reconstructs_training_span(self):
        rng = np.random.default_rng(29)
        latent = rng.normal(size=(100, 3))
        projection = rng.normal(size=(3, 8))
        x = latent @ projection + np.linspace(-2.0, 2.0, 8)
        model = pca.fit(x, 3)
        np.testing.assert_allclose(pca.predict(x, model), x, atol=1e-12)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
