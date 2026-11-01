# Joint semantic continuity fit for the Luigi walk

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
