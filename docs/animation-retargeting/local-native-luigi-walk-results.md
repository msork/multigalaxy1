# First bilateral local-native Luigi walk fit

## Method and inputs

`fit_luigi_walk_semantic_local_native.py` now reads only the real bilateral raw
ANM training samples. It fits left and right independently with 96 samples per
frame and side; no right-side samples are reflected from the left. The fit
uses the shared rest-calibrated 11D semantic extractor, including signed
radial/tangential/depth hand motion and torso-relative forearm roll. It maps
semantic changes to arm transforms relative to the torso, then composes those
transforms onto the validated Waluigi walk baseline. The baseline keeps the
walk body, cap, legs, and root motion. Both fist poses remain fixed, and the
wrist-to-glove constraint is applied exactly.

One quadratic optimization spans all 61 frames and penalizes final arm pose
SE(3) velocity and acceleration while keeping each frame within a trust ball
around its local affine prediction. The result is evaluated after baseline
composition, fist locking, and glove attachment. No post-bake smoothing is
used. A candidate preview is written only if all numerical gates pass; a
passing candidate must then pass a save/reopen matrix comparison before it is
kept at the preview path.

## Result

The numerical gate failed, so no preview or baked action was created. The
global scalar coverage audit passes, but the per-frame 96-neighbor joint
semantic neighborhoods do not support many combined target vectors: 38/61
left and 37/61 right frames fail local support after final composition. The
candidate was rejected rather than extending outside those neighborhoods.

Excursion retention was left/right: shoulder `39.2% / 81.8%`, elbow
`86.1% / 91.3%`, wrist `65.6% / 98.8%`. Group semantic RMSE:

| Feature group | Left | Right |
| --- | ---: | ---: |
| Shoulder orientation | 0.2201 | 0.0835 |
| Elbow flexion | 0.1402 | 0.1473 |
| Forearm roll | 0.2485 | 0.0377 |
| Wrist orientation | 0.0884 | 0.0717 |
| Arm-plane hand position | 0.1263 | 0.1037 |

Maximum final-pose jump was `0.1642` normalized SE(3), from frame 9 to 10,
with `bone_77` the largest contributor. Maximum glove gap was
`2.29e-16` armature units; fixed-fist local-transform error was `1.55e-15`.
The largest semantic residual was left `shoulder_rot_x`, frame 22, at `0.6996`
(target `-0.0647`, Luigi `0.6350`). The worst forearm-roll residual was left
frame 26 at `0.4266`.

The torso-relative roll correction greatly reduced right roll RMSE from `0.332`
to `0.0377` and left from `0.701` to `0.2485`. Scalar semantic coverage remains
0 unsupported frames per side/dimension, but these results show that scalar
coverage alone does not guarantee joint local support or adequate motion
retention. Do not visually review a preview from this run; none was emitted.

Numerical diagnostics:

- `tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_local_native_arm_plane_v1.json`
- `tools/blender/reports/luigi_walk_bilateral_semantic_coverage_v3_torso_neutral_roll.json`
