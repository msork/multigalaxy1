"""Small pure-NumPy regression tests; run inside Blender's bundled Python."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import factorized_semantic_deformation as model


def test_factor_design_and_interactions():
    rng = np.random.default_rng(13)
    x = rng.normal(size=(80, 11))
    design, blocks = model.factorized_design(x)
    assert design.shape == (80, 57)
    assert "elbow_flexion_x_hand_radial" in blocks["interactions"]
    assert set(blocks["factors"]) == set(model.FACTOR_GROUPS)
    replay, _ = model.factorized_design(
        x[:5], np.asarray(blocks["mean"]), np.asarray(blocks["scale"]),
        blocks["quadratic_center"])
    assert np.all(np.isfinite(replay))


def test_action_group_cv_does_not_split_actions():
    ids = np.repeat([f"action_{i}" for i in range(20)], 4)
    folds = model.action_group_folds(ids, 5)
    for name in set(ids):
        assert len(set(folds[ids == name])) == 1


def test_factorized_and_global_models_recover_quadratic_signal():
    rng = np.random.default_rng(27)
    x = rng.normal(size=(240, 11))
    y = np.column_stack((x[:, 0] + 2.0*x[:, 3]*x[:, 8],
                         x[:, 5]*x[:, 9]))
    actions = np.repeat([f"action_{i:02d}" for i in range(30)], 8)
    design, state = model.factorized_design(x)
    fitted = model.fit_ridge_cv(design, y, actions, alphas=(1e-4, 1e-2, 1.0))
    pred = model.predict(fitted, design)
    assert np.sqrt(np.mean((pred-y)**2)) < 0.03
    global_design, _ = model.global_quadratic_design(x)
    assert global_design.shape == (240, 78)


if __name__ == "__main__":
    test_factor_design_and_interactions()
    test_action_group_cv_does_not_split_actions()
    test_factorized_and_global_models_recover_quadratic_signal()
    print("factorized semantic deformation tests passed")
