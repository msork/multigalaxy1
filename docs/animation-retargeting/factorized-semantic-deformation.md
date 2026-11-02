# Factorized semantic-to-deformation experiment

## Objective and boundary

This milestone tests whether a factorized supervised map can reconstruct native Waluigi arm poses from the corrected raw bilateral ANM semantics. It does **not** fit Luigi motion. Training uses only the 118 raw-ANM training actions; all 27 complete-action holdouts remain validation-only. Left and right are independent models trained on their own raw samples. The raw gzip and all source assets remain external/ignored.

The production rig, original actions, intentional object scale, workfile, and validated walk are read-only. Evaluation is performed on a disposable duplicate. Preview creation is conditional on every native holdout passing the numerical deformation gate; a failed run must not leave a preview artifact.

## Model

Each model maps the 11 rest-calibrated anatomical features to the eight bones in that side's complete two-branch arm profile. Outputs are per-bone rest-relative SE(3) coordinates (translation normalized by arm length and rotation logarithm), expressed relative to the shared torso rest frame so source torso motion is retained at composition time. All eight bone outputs are predicted jointly so the overlapping branches are not treated as independent chains. The source sample's shared torso/root and non-arm context are retained. Finger local transforms are fixed to the validated closed-fist baseline and are not predicted from native hand channels.

The five additive main effects are shoulder orientation (3D), elbow flexion, forearm roll, wrist orientation (3D), and arm-plane hand position (radial/tangential/depth). Each factor uses linear and within-factor quadratic terms. Only audited cross-factor terms are added: shoulder×elbow, shoulder×wrist, elbow×wrist, wrist×hand-position, and elbow×hand-radial. The right model includes the same symmetric term set. A single regularized least-squares fit predicts all 48 arm transform coordinates; design-block contributions remain individually inspectable. The architecture does not average native poses or select neighbors.

Per-factor confidence is estimated from training-only marginal and factor-group standardized distance/range support. Cross-term confidence uses joint support for the corresponding pair and records action coverage and design leverage. These values diagnose extrapolation; they are not used to hide failed holdouts.

The comparison baseline is a global second-order polynomial ridge inverse over all 11 features with the same transform targets. Regularization is selected by action-grouped cross-validation within training actions only. Holdout actions are evaluated once after model selection.

## Composition and preservation

Both independently predicted arm poses are composed onto the exact native sample torso/root transform. Fingers are set to the validated closed-fist local transforms, then each glove root and its descendants are shifted together to satisfy the wrist attachment constraint. The shoulder, main arm, overlap branch, forearm, and hand/glove transforms remain joint model outputs. No constraints are added to the rig.

## Validation and preview gate

For every sample in all 27 holdout actions, report semantic reconstruction, per-bone rest-relative translation/rotation errors, shoulder/elbow/wrist/glove landmark errors, weighted arm-mesh centroid errors, glove gap, fixed-fist local-transform error, finite values, and object-scale preservation. Also report action-level results and worst action/frame/bone.

The validation thresholds are fixed before evaluation: arm bone rotation RMSE at most 5 degrees; translation RMSE at most 0.02 upper-arm lengths; 95th-percentile key landmark error at most 0.02 upper-arm lengths; 95th-percentile weighted arm centroid error at most 0.02 upper-arm lengths; maximum wrist/glove gap at most 0.02 upper-arm lengths; fixed-fist local error below `1e-6`; and no NaN/Inf or scale change. The report must distinguish model output error from the deliberate fixed-fist and glove-attachment composition adjustments.

The measured wrist/glove gap after composition is not sufficient by itself: the 95th-percentile attachment correction must also remain at most 0.02 upper-arm lengths, and each overlap-branch relative-orientation error must remain at most 0.05 radians at the 95th percentile. These checks prevent hard composition from hiding a disconnected predicted glove or a collapsed branch relationship.

Only if every holdout passes is a disposable preview blend created. It shows one complete target Waluigi at a time, with the native reference hidden; the saved action is baked and has no runtime constraints. A failed numerical gate emits only a JSON report and no preview.

The first factorized native holdout evaluation is documented in [factorized semantic deformation results](factorized-semantic-deformation-results.md). It failed the gate; no preview was emitted.

## Interpretation

This is an exploratory model-family comparison, not an approved retargeter. A good training fit is insufficient. If holdouts fail, inspect factor support and action-level residual patterns, revise representation or training coverage, and keep Luigi fitting blocked. A global inverse baseline is comparative only and must not become automatic based on lower average error without passing the same per-action, branch, mesh, and fist gates.
