# Joint semantic continuity fit for the Luigi walk

> Historical v11 results below passed the elbow-only numerical gate but later
> failed visual review due to full-arm/context pose switching. They are not a
> validated final walk. Current retry status and the remaining blocker are
> recorded later in this document.

## Result

The left/right semantic walk is now fit jointly over frames 0–60 before pose
application and baking. The bake/save pipeline was not changed. The final
preview is
`tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v11.blend`.
It uses the existing validated baseline and complete textured Waluigi rig.

| Measure | Previous v9 | Joint v11 |
|---|---:|---:|
| Maximum left elbow step | 0.7603 rad | 0.0450 rad (30→31) |
| Maximum right elbow step | 0.4991 rad | 0.1360 rad (50→51) |
| Left 41→42 elbow step | 0.7603 rad | 0.0254 rad |
| Right 50→51 elbow step | 0.4991 rad | 0.1360 rad |
| Maximum left branch-gap excursion outside raw native range | 0.0279 | 0.0085 armature units |
| Maximum right branch-gap excursion outside raw native range | 0.0171 | 0.0102 armature units |

All elbow steps are below `0.15` rad. Both sides' branch-gap excursions are
inside the validated `0.02` armature-unit tolerance. The full fit gate passes.

## Cause and solver change

The former method solved each frame independently, then smoothed latent
coefficients. Its nearest native pose prior switched actions around the bad
transitions: left `Waluigi_11_Reactions_seq6` →
`Waluigi_6_Fielding_seq21` at 41→42; right `Waluigi_8_Running_seq3` →
`Waluigi_6_Fielding_seq21` at 50→51. Latent smoothing alone did not constrain
elbow-feature velocity on the nonlinear pose manifold, so the elbow jumped
while shoulder and forearm motion remained plausible.

The new character-agnostic matrix-free Gauss-Newton sequence solver optimizes
all 61 latent states together. The objective combines per-frame semantic fit,
native latent prior, latent velocity/acceleration, explicit elbow velocity and
acceleration tracking of Luigi's source timing, and per-frame plus temporal
continuity terms for shoulder, both elbow branches, and wrist/glove gaps. The
existing shoulder, forearm, wrist, and hand semantic feature weights are
unchanged. No post-bake smoothing is applied.

Elbow RMSE improved from `0.2198` to `0.0255` rad on the left and from
`0.1913` to `0.0465` rad on the right. Changes in other feature RMSEs versus
v9 are small: left shoulder axes `-0.0044/+0.0080/+0.0048` rad, forearm roll
`-0.0013` rad; right shoulder axes `+0.0068/+0.0018/-0.0067` rad, forearm
roll `-0.0020` rad. Wrist/hand deltas for every feature are recorded in the
fit JSON.

## Saved preview validation

The reopened action passes the established fit-to-save gate across frames
0–60: maximum pose matrix difference `3.34e-6` (`bone_49`, frame 12), maximum
local-basis difference `4.77e-7`, maximum semantic RMSE report delta
`4.53e-7` left / `1.53e-7` right. All 61 integer frames are keyed with linear
interpolation, quaternion sign-flip count is zero, object scale remains
`43.437`, and runtime constraints are zero.

Local diagnostic reports (ignored by Git):

- Fit: `tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v11_fit.json`
- Saved validation: `tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v11_saved_validation.json`

The fit report includes nearest native actions, pre-joint and final elbow
values, latent coefficients, branch gaps, and objective history for frames
38–44 and 47–53.
# Full-context semantic continuity follow-up

## Failure evidence

The v11 fit met its elbow and branch-gap checks, but visual review still showed
snaps. A rest-relative SE(3) audit over all 38 modeled bones on both sides
found the largest adjacent jump at bone_77 from frames 49 to 50: normalized
SE(3) 4.9815 (rotation 1.6365 rad and translation 2.557 armature units).
Similar full-context jumps occur at 32->33 and 41->42. Bone_77 is a mirrored
hand/finger context bone, demonstrating that elbow-only continuity does not
protect the whole arm or glove attachment. v11's maximum latent step was
22.5, consistent with distant pose-neighborhood switching.

## Fit objective update

The next fit continues to optimize all frames together, but regularizes the
decoded full pose instead of only selected semantic channels. For every
modeled bone it adds first- and second-difference penalties on the six
rest-relative pose coordinates (rotation-vector and normalized translation).
It also regularizes arm-region weighted-mesh centroid trajectories and the
explicit wrist/glove attachment gap. The Luigi semantic trajectory remains
the velocity and acceleration target for semantic channels, preserving source
timing. A hinge penalty limits large adjacent latent steps to discourage
switching between distant native-pose neighborhoods. A component-wise hinge
on normalized rest-relative pose velocity additionally limits large per-frame
changes for every modeled bone. These terms act during the fit; no output
smoothing is applied after baking.

The first full-pose retry showed that constraining PCA-standardized features
did not constrain the reported physical SE(3) metric (its worst jump only
fell from 4.98 to 4.06). The pose-velocity channels now use actual decoded
rest-relative rotation-vector radians and translation divided by upper-arm
length, matching the acceptance metric's units.

Centroid features are computed from weighted vertex-group rest centroids
transformed by the candidate bone poses, in torso-relative coordinates. They
cover arm union, shoulder, upper arm, elbow/overlap, forearm, and glove/hand
regions independently for each side. The fit report records full-context
SE(3) jumps and the requested 31->33, 40->43, and 48->51 windows, plus glove
gap maxima. The existing baseline, preview construction, and bake/save path
remain unchanged.

## Retry results and remaining blocker

The first retries did not pass acceptance. v12 reduced the worst full-pose jump
from 4.9815 to 4.5327; v13's normalized-channel hinge reduced it to 4.0573;
v14, after switching that hinge to physical SE(3) units, reduced it to 3.9227.
The maximum remains bone_77 at 49->50 (1.2392 rad, 2.0228 armature units).
Requested windows still contain large adjacent jumps: 32->33 is 3.0003,
41->42 is 2.5196, and 49->50 is 3.9227. v14's maximum wrist/glove gap is
0.02010 armature units, just over its 0.02000 gate. v14 also substantially
degraded semantic residuals, including forearm-roll RMSE to 1.304 rad left
and 1.249 rad right. Its preview is a failed diagnostic artifact, not a
visual-review candidate.

The remaining structural cause is that the optimizer's continuity features
are evaluated on decoded PCA poses before the existing application
composition, while the baked pose is subsequently rebased to validated-walk
torso transforms, mirrored into the right rest basis, shoulder-anchored, and
has right finger transforms rebuilt from the walk baseline. The gate measures
the composed/baked pose, so the optimization and acceptance paths are not yet
the same transform function. This explains why improving the decoded pose
penalty has not removed the final bone_77 jump and why semantic residuals
degrade before the post-application gate passes. The next implementation must
evaluate the same baseline-rebase and right-side composition inside the joint
objective before further tuning its weights.

## Acceptance

Require maximum adjacent full-context normalized SE(3) jump <= 0.75, glove
gap within the validated native range plus 0.02 armature units, all previous
baseline/binding and saved-action gates to pass, and visual review to show no
snap, glove separation, or arm flailing over frames 0-60. Numerical gates do
not substitute for viewport review.
