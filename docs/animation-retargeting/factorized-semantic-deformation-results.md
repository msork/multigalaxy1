# Factorized semantic deformation: native holdout result

## Data and model

This evaluation uses the corrected bilateral raw-ANM v2 dataset only: 118 complete training actions (4,116 samples per side) and 27 complete validation-only actions (1,168 samples per side). No Luigi walk was fitted. A disposable duplicate and its bound meshes were used for evaluation; production objects/actions were not changed.

Each independently trained side model predicts 48 transform coordinates for the eight bones in its two overlapping arm branches. Coordinates are torso-rest-frame-relative per-bone rest-relative SE(3), with translation scaled by upper-arm length. Five additive blocks model shoulder orientation, elbow flexion, forearm roll, wrist orientation, and arm-plane hand position. The design has 57 columns: intercept, factor-specific linear/within-factor quadratic terms, and the selected cross terms. Cross terms are shoulder×elbow, shoulder×wrist, elbow×wrist, wrist×hand-position, and elbow×hand-radial. The global supervised baseline uses all-feature second-order polynomial terms (78 columns). Both use ridge regularization selected by five-fold complete-action CV; both sides selected alpha 100.

Both bilateral outputs are composed together. The closed-fist finger locals come from `Waluigi_Arm_Test` with `Waluigi_GalaxyWalk_Legs_Baked` at frame 0. Wrist/glove correction moves the glove subtree together. Overlap branches are predicted jointly, not mirrored or collapsed.

## Holdout errors

| Metric | Factorized model | Global quadratic baseline |
| --- | ---: | ---: |
| Arm translation RMSE (upper-arm lengths) | 0.155 | 0.148 |
| Arm rotation RMSE | 0.541 rad (31.0°) | 0.507 rad (29.0°) |
| Landmark error p95 (upper-arm lengths) | 0.641 | 0.605 |
| Landmark error max (upper-arm lengths) | 2.065 | 1.997 |
| Semantic absolute error RMSE | 0.695 | 0.700 |
| Weighted arm centroid error p95 | 32.75 world units | Not evaluated |
| Weighted arm centroid error max | 69.94 world units | Not evaluated |

The normalized upper-arm world length is 23.608 units, so factorized weighted centroid p95 is about 1.39 upper-arm lengths. The factorized model does not meet any pose, landmark, or mesh gate.

Per-side semantic RMSE by group (radians for orientation/bend/roll, calibrated units for hand placement):

| Group | Left | Right |
| --- | ---: | ---: |
| Shoulder orientation | 0.041 | 0.039 |
| Elbow flexion | 0.206 | 0.145 |
| Forearm roll | 0.955 | 1.253 |
| Wrist orientation | 0.666 | 0.631 |
| Arm-plane hand position | 1.191 | 0.658 |

The glove gap after composition is at most `2.61e-6` upper-arm lengths and fixed-fist local error is below `3.2e-15`. However, the correction required to get that gap is itself excessive: p95 is 0.680 upper-arm lengths left and 0.405 right. Thus the hard attachment step conceals badly mismatched wrist/glove predictions. Relative branch orientation p95 errors range from 0.063 rad (right overlap) to 0.296 rad (left main pair), with larger maxima. These fail the added branch and attachment-correction gates.

Worst factorized holdout cases are `Waluigi_7_Batting_seq24` for landmark error (p95 1.043 upper-arm lengths) and `Waluigi_11_Reactions_seq4` for weighted centroid error (p95 54.46 world units). The worst bones are the two glove roots: `bone_30` has 1.171 rad rotation RMSE and `bone_65` 1.026 rad.

## Support and diagnosis

Holdout factor support remains weak despite strong individual scalar coverage. Outside-training-range sample counts on the left are 40 for shoulder orientation, 15 for wrist orientation, and 40 for hand-plane position. The most weakly supported audited interactions include 55 left samples outside the wrist×hand-position axis range, 45 outside shoulder×wrist, and 40 outside shoulder×elbow. On the right, 9 shoulder, 1 elbow, and 7 hand-plane samples lie outside their marginal training ranges; 10 samples lie outside shoulder×elbow and 9 outside shoulder×wrist. Confidence p05 and full per-factor/cross-term support statistics are in the JSON report.

The global inverse baseline is marginally better on pose and landmark errors and virtually tied on semantic error. The factor decomposition therefore did not resolve the many-to-one semantic→pose ambiguity. Cross-factor terms also do not provide enough native support for the left shoulder/wrist/hand combinations. Large glove corrections and branch residuals confirm that coherent deformation cannot be assumed from semantic fit alone.

The transform encoding/decoding check has maximum arm-matrix error `1.25e-6`; the Blender identity replay has maximum pose matrix error `1.03e-6` and arm weighted-centroid error `3.61e-5` world units. The large holdout error is therefore model generalization failure, not a coordinate-space or mesh-binding mismatch.

## Gate result

The native reconstruction gate **failed**. No `.blend` preview was generated. No solver was run on Luigi walk. Continue research on additional conditioning that resolves native pose ambiguity (for example, motion-family/phase or semantic velocity/history) and improve supported shoulder/wrist/hand combinations before any source retarget.

Machine-readable results: `tools/blender/reports/factorized_native_semantics_v1.json`. Rerun with `tools/blender/fit_factorized_native_semantics.py` in Blender 5.2.3; the raw dataset remains external and ignored.

Reproduce the evaluation from the saved validation scene with:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' -b `
  'tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend' `
  --python tools/blender/fit_factorized_native_semantics.py -- `
  --dataset tools/blender/reports/native_waluigi_raw_anm_bilateral_v2.json.gz `
  --report tools/blender/reports/factorized_native_semantics_v1.json `
  --preview tools/blender/reports/factorized_native_semantic_holdout_v1.blend
```
