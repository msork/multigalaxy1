# Local native deformation prototype

## Model

The fitter uses only the raw-ANM production-basis calibration. For each of the
61 Luigi semantic samples it selects nearby native samples in the calibrated
22-feature left/right semantic space and fits a weighted local affine map from
semantic deltas to the complete 38-bone rest-relative SE(3) feature vector.
The local Jacobian therefore distributes each semantic change over the native
deformation context instead of synthesizing every target bone independently.

Forward and backward passes carry adjacent frames' native neighborhoods as a
continuity prior. A local semantic support box and pose trust radius prevent
the affine map from extrapolating without bound. Both hands use fixed local
finger transforms from the read-only Waluigi reference pose. Wrist-to-glove
attachment is constrained by moving the glove root and its descendants onto
the wrist endpoint before semantic and continuity validation. The validated
legs/torso walk baseline, cap, mesh bindings, and object transforms are kept by
the existing preview builder. There is no post-fit smoothing.

## Current result and limits

The first local fit did not pass the preview gate and produced no `.blend`.
It passes the numerical glove gap (`2.5e-16` armature units), fist lock
(`1.6e-15` local-transform error), and max pose step (`0.225`). It fails the
semantic excursion and fit gates, and all 61 frames fail the strict local
support-box check. The principal unsupported feature is right shoulder Y on
all frames; signed hand X is unsupported on 37 left and 28 right frames.
Shoulder RMSE is 0.48 / 0.51 rad, elbow RMSE 0.27 / 0.44 rad, and forearm-roll
RMSE 0.87 / 0.84 rad. The fitted elbow excursion is too large (134% / 144%),
while wrist excursion is slightly low (79%).

The raw calibration profile contains only the left Waluigi arm. Right-side
native semantics are therefore produced by the validated homologous mirror
map; this prototype does not treat those as measured right-arm examples. The
right shoulder support failure and the remaining semantic errors mean that
this local model is not ready for visual review. Do not widen its support box
to force a preview. The next data requirement is native right-arm calibration
or a validated side-specific semantic correspondence, followed by a constrained
local solve that fits hand position and branch deformation without exceeding
Luigi's semantic excursion.

The detailed frame-by-frame result is in
`tools/blender/reports/luigi_walk_semantic_local_native_v1.json`.
