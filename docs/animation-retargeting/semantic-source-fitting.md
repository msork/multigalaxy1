# Semantic Source Fitting: First Left-Arm Walk Prototype

## Scope and safety boundary

This milestone maps only Galaxy Luigi's `luigi.001_armature_walk` action over
frames 0–60 to the validated 38-bone Waluigi left-arm context. It uses
disposable armatures and mesh/data copies. The production Waluigi rig, its
actions, the source Luigi rig, and the native calibration data remain
read-only. No right-arm or full moveset work is in scope.

The target pose profile and PCA basis come only from
`native_waluigi_arm_context_production_basis.json.gz`. Training uses its 23
training actions and 845 samples. The five complete native holdouts remain
validation-only. Source action channels are read from the action slot's FCurves;
the orphaned slot is not rebound to a duplicate, since that path was measured
to produce incorrect pose matrices. Quaternion channels are normalized before
pose hierarchy composition.

## Character-neutral profile interface

Source and target profiles name the torso, shoulder, upper-arm, forearm, hand,
and target deformation-branch bones. They also identify arm-side handedness,
rest matrices, arm lengths, the PCA/context bone list, meshes, and the action
slot. Wario can use the same feature and solver code with a separate verified
Wario target profile and calibration dataset.

The semantic vector contains 11 values:

| Feature | Dimensions | Definition |
| --- | ---: | --- |
| Shoulder orientation | 3 | Rest-relative upper-arm orientation against torso, including the source shoulder-parent transform, quaternion log in the shared rest torso frame |
| Elbow flexion | 1 | Signed/unsigned calibrated angle change between upper- and lower-arm segment directions |
| Forearm roll | 1 | Twist about the forearm's rest long axis; Waluigi uses the calibrated blend of its two overlapping branches |
| Wrist orientation | 3 | Rest-relative hand orientation against the forearm, expressed in a shared rest limb frame |
| Hand position | 3 | Hand endpoint relative to shoulder, in torso coordinates and normalized by rest arm length |

Source and target use rest-calibrated frames, rather than copying local
quaternions between different rigs. Target branch geometry is measured from
both Waluigi branches; fingers and glove context are represented in the target
38-bone PCA output, not independently driven from Luigi finger channels.

## Target mapping and per-sequence solve

Fit a regularized RBF kernel regression from semantic state to latent
coefficients as an initial nonlinear baseline. Select kernel width and ridge
strength with action-grouped folds from the 23 training actions only. The
first implementation found that this direct regressor extrapolates poorly for
the Luigi walk, so its output is diagnostic and is not used as the final pose.

For Luigi frames, evaluate the 11-dimensional semantic vector. Start at the
nearest native latent pose and invert the PCA model locally with damped
Gauss-Newton. Each frame minimizes semantic residual, native pose distance, and
four Waluigi shoulder/elbow/overlap/wrist joint-gap residuals. Then solve the
61-frame quadratic sequence objective:

```text
E(z) = sum_t ||z_t - z_semantic_inverse_t||^2
     + lambda_native sum_t ||z_t - z_nearest-native_t||^2_D
     + lambda_velocity sum_t ||z_(t+1) - z_t||^2_D
     + lambda_acceleration sum_t ||z_(t+1) - 2 z_t + z_(t-1)||^2_D
```

The nearest-native term provides a local pose prior, while velocity and
acceleration terms suppress frame jitter and abrupt pose changes. Joint-gap
terms discourage tearing between the overlapping Waluigi branches. Coefficients
are decoded through the target PCA basis into rest-relative transforms.

## Walk preview composition

The decoded arm/finger/context pose is applied only to a disposable full
Waluigi rig with duplicated meshes and accessories. Preserve the target walk's
validated leg and torso channels where available. Rebase the predicted arm
pose to the walk torso frame before solving local bases, so the target walk
torso remains intact while the source semantic arm motion follows it. Bake a
new action name on the duplicate; no runtime constraints remain. The preview
contains the whole Waluigi character and is saved as a local `.blend` ignored
by Git.

When the running scene has the baked walk action but the saved input file does
not, export that single action's curves over the loopback bridge with
`python tools/blender/export_walk_action_curves.py --action Waluigi_GalaxyWalk_Legs_Baked --output tools/blender/reports/waluigi_walk_action_curves.json`.
The fitting script can import this local curve snapshot onto its disposable
target copy; the original live action is never reassigned or edited.

## Validation and stop condition

Record source semantic feature ranges, native semantic coverage distance,
kernel cross-validation baseline, latent coefficient ranges, objective terms,
finite-value/scale checks, and target shoulder/elbow/wrist/glove/finger landmarks.
Reconstruct the target PCA profile and compare its matrices/landmarks to the
solved latent values. Check frames 0, 15, 30, 45, and 60 for full-character
visual inspection. A numerical pass is not visual acceptance. Do not proceed
to right-arm, full-body semantic fitting, or another source action until the
user accepts the left-arm walk preview.
