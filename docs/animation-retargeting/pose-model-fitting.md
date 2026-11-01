# Exploratory Native Arm Pose Model

## Scope and data boundary

This milestone fits an exploratory production-basis model for the eight Waluigi
left-arm bones `bone_22`, `bone_23`, `bone_24`, `bone_25`, `bone_26`,
`bone_27`, `bone_29`, and `bone_30`. It consumes only
`native_waluigi_arm_calibration_production_basis.json.gz`. The 23 training
actions supply all feature normalization, PCA bases, nonlinear basis centers,
hyperparameter selection, and final model parameters. The five complete
holdout actions are not used until the model and its settings are frozen; they
are used only for reconstruction metrics and preview generation.

The feature vector concatenates one rest-relative SE(3) transform per bone.
Each transform is represented as translation plus a sign-invariant quaternion
rotation logarithm (six values). Every bone remains an independent feature
block; the two deformation branches are not fused into shared joints. Scale is
checked as approximately one and is not learned. Standardization and every
data-dependent transform are fit on training samples only.

## Compared models

1. **PCA linear baseline:** project standardized 48D samples into a compact
   latent space and reconstruct with the corresponding principal components.
   Latent size is chosen by grouped cross-validation, with complete training
   actions kept together in each fold.
2. **RBF decoder:** use the same PCA latent encoder and a compact radial-basis
   decoder to predict the full standardized transform vector. The centers,
   kernel width, and ridge regularization are fit/selected using training
   actions only. This gives a nonlinear decoder while preserving the same
   measurable compact latent.

The fitting module uses NumPy supplied by Blender; it does not require a Python
package install or network access. It writes model artifacts locally under the
ignored reports directory. Committed outputs are code, this specification, and
small JSON metric summaries only.

## Validation and reconstruction

Cross-validation groups by action, never by random frame. After settings are
fixed, each held-out action is reconstructed over all of its stored exact
key/subframe samples and endpoints. The final report includes per-bone angular
and translation errors, branch landmark endpoint errors, production-mesh
weighted centroid errors, and time-normalized velocity/acceleration residuals.
Pose-space metrics are computed from transform features; mesh and endpoint
metrics are also measured by evaluating reconstructed animation on temporary
duplicates of the production-basis rig and mesh.

The Blender preview tool reads the frozen model and dataset, creates only
temporary duplicate rigs/meshes/actions, reconstructs each holdout action, and
saves a separate preview `.blend`. The input autosave and production object are
never saved or modified. Preview acceptance requires visual continuity of the
shoulder, both elbow/forearm branches, wrist, and glove on all five holdouts.
Passing numeric reconstruction scores alone does not open the semantic-source
fitting gate.

## Reuse boundary

The model and feature code take bone names, rest transforms, object basis,
weighted meshes, and action split from a target profile/dataset rather than
hard-coding gameplay or Luigi source mappings. The current profile is Waluigi.
Wario requires its own verified native calibration dataset and profile; its
data must not be mixed into this fit.

## Acceptance gate

Do not start semantic Luigi-to-Waluigi fitting until the committed metrics have
been generated from the production-basis dataset and all five reconstructed
holdouts have passed viewport review. Any visual failure keeps this milestone
exploratory and blocks that next step.

## Exploratory fit results

The first fit used 838 samples from 23 actions; the five holdouts contributed
262 samples strictly after grouped five-fold training-action cross-validation.
The feature space is 48D. The one-standard-error PCA selection with a
`1e-10` numerical-tie floor chose 40 latent dimensions, so this is only a
moderate reduction. The nonlinear comparison is an RBF decoder with the same
40D PCA encoder and 48 radial centers; grouped CV selected width factor `3.0`
and ridge `1e-4`.

PCA reconstruction was almost exact: standardized feature RMSE was
`1.60e-7` train / `6.87e-7` holdout; the worst held-out bone was `bone_26`,
with translation RMSE `0.000023` world units and rotation RMSE below
`0.000001` degrees. Holdout landmark RMSEs were all below `0.00012` world
units. Its temporal acceleration residual was `2.24e-5` standardized units per
frame squared. These scores show the observed eight-bone transforms lie close
to a 40D linear subspace; they do not demonstrate semantic compression.

The RBF model generalized poorly: feature RMSE was `0.0382` train / `0.2945`
holdout. The worst held-out rotational RMSE was `34.73 degrees` on `bone_25`
(maximum single-sample angular error `179.86 degrees`); worst held-out
translation RMSE was `17.96` world units on `bone_22`. `sequence_20.001` had
the largest per-action rotational error (`66.07 degrees` on `bone_25`), while
`sequence_15.003` had the largest feature and glove-tip errors. Its holdout
glove-tip landmark RMSE was `18.15` world units and acceleration residual was
`4.61` standardized units per frame squared.

The PCA model exposes a second issue: although its eight requested bone poses
and endpoints reconstruct accurately, evaluating those poses on the production
mesh does not reproduce native weighted centroids. PCA holdout centroid RMSE
reaches `22.68` world units for `bone_22`, `3.82` for `bone_23`, and `0.075`
for `bone_30`; `sequence_15.003` is worst at `45.44` units for `bone_22`.
RBF is worse, reaching `30.81` world units for `bone_22`, `17.41` for
`bone_23`, and `17.37` for `bone_30`. This proves the eight-transform feature
set does not by itself reproduce the mesh deformation; inspect weighted
vertices and expand context-bone coverage before fitting source semantics.

`tools/blender/fit_native_arm_pose_model.py` writes local ignored model and
prediction `.npz` files plus a full local metrics report. The Blender preview
tool reconstructs all exact samples for each held-out action on duplicated
production rigs and meshes, with all other bones left neutral. The saved file
`tools/blender/reports/native_arm_pose_model_previews.blend` contains the
disposable rigs `Exploratory_PCA_Arm_Rig` and `Exploratory_RBF_Arm_Rig`, with
all five actions per rig named `Exploratory_<model>_<sequence>`. Both copies
retain scale 43.437; the production armature has no assigned model action.
Inspect the shoulder/torso blend, both elbow/forearm branches, glove attachment,
and temporal motion in every sequence. Because centroid and RBF pose errors are
large, semantic source fitting remains blocked pending visual review and a
broader target context model.

Compact committed metrics live in
`tools/blender/reports/native_arm_pose_model_summary.json`. Full per-action
metrics, fitted weights, predictions, and the preview `.blend` are generated
locally and ignored. `test_native_arm_pose_model.py` exercises quaternion sign
equivalence, feature encoding, PCA reconstruction, and RBF output shape under
Blender's bundled Python.
