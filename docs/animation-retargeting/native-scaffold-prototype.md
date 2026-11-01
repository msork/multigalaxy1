# Native locomotion scaffold prototype

## Scope

This is an isolated experiment for Luigi's Galaxy walk, frames 0–60. It leaves
the existing graph fitter and production rigs/actions unchanged. The primary
scaffold is the raw-ANM `Waluigi_8_Running_seq10` clip; the shorter
`Waluigi_6_Fielding_seq21` clip is evaluated independently as a comparison.

Run the fit from the worktree with Blender 5.2.3:

    & 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' -b 'tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend' --python tools/blender/fit_luigi_walk_native_scaffold.py -- --dataset 'tools/blender/reports/native_waluigi_raw_anm_calibration.json.gz' --report 'tools/blender/reports/luigi_walk_native_scaffold_v1.json' --preview 'tools/blender/reports/luigi_walk_native_scaffold_v1.blend'

Reopen-validate the generated preview:

    & 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' -b 'tools/blender/reports/luigi_walk_native_scaffold_v1.blend' --python tools/blender/validate_native_scaffold_preview.py -- --output 'tools/blender/reports/luigi_walk_native_scaffold_v1_reopen_validation.json'

## Sequence method

1. Extract Luigi's 61 evaluated poses from its explicitly selected action slot.
2. Express Luigi and Waluigi shoulder, elbow, forearm twist, wrist, and hand
   features in rest-calibrated anatomical frames. Elbow is the absolute angle
   from a straight limb. Hand position remains a signed three-component value.
3. Interpolate each native sample interval at quarter-sample increments in
   rest-relative SE(3). Form candidate states only from one scaffold action at
   a time. A bounded monotonic dynamic program maps each Luigi frame to nearby
   native samples, with phase deviation, semantic residual, native-pose
   transition, and source-motion timing costs. It cannot jump between actions.
4. Preserve the selected native 38-bone context pose. Compose it over the
   validated walk baseline using the existing arm mirror/torso-anchor and
   finger-context rules. This is the pose evaluated by continuity and glove
   checks.
5. Generate a disposable preview only if motion retention, final-pose jump,
   glove-gap, finite-transform, and per-bone spike gates all pass.

Quarter-sample pose interpolation and bounded neighboring native states are
the only local adjustments in this prototype; it does not expose free latent
coordinates. No post-bake smoothing is permitted. Phase mapping and
interpolation fractions are reported frame by frame.

## Safety and validation

The source Waluigi, source actions, and raw calibration dataset are read-only.
The tool does not modify or assign actions on them. Any preview rig, copied
meshes, and output action have disposable names; output is saved to a new file.
The intentional 43.437 object transform is checked and never applied.

Before preview generation, report both semantic residuals and source-motion
retention, the full modeled-context normalized SE(3) step, branch joint gaps,
and per-bone outliers. Preview output is suppressed unless all requested
numerical gates pass. Scaffold comparisons are independent runs, not a shared
graph.

## Current limitations

The native calibration stores a 38-bone context profile, not every Waluigi
bone. Unmodeled pose context therefore comes from the validated walk baseline.
The right native arm is inferred through the established mirror composition;
raw calibration directly measures only the left profile. A passing numerical
gate remains a candidate for human viewport review, not proof of visual
correctness.

## First prototype result

The primary Waluigi_8_Running_seq10 path passed the requested numeric gates:
shoulder/elbow excursion retention was 133%/397% on the left and 133%/426% on
the right; maximum final modeled-context step was 0.49825 (bone bone_81,
frames 19 to 20); and maximum glove gap was 0.00748 armature units. The
selected phase begins at native time 0.10 (sample 6) and ends at 0.9375
(between samples 56 and 57), so it uses a phase offset and compresses the
sampled native span.

Semantic error remains material. Compared with direct linear phase, left-side
RMSE changes modestly: elbow 1.033 to 0.997 rad, roll 2.027 to 2.022 rad,
wrist 0.236 to 0.210 rad, and hand position 0.565 to 0.531. Right shoulder
RMSE rises slightly (1.008 to 1.032 rad); the other right-side groups improve.
Combined left/right RMSE is 0.798 rad for elbow flexion and 1.534 rad for
forearm roll. The target
left elbow range is 0.179 to 2.177 rad versus Luigi's 0.175 to 0.678 rad; the
retention gate passes by range, but Waluigi over-flexion is a clear visual risk.
The Fielding_seq21 comparison has lower semantic errors in several groups,
but its maximum glove gap is 0.03235 and it does not pass.

The passing Running candidate was baked to
tools/blender/reports/luigi_walk_native_scaffold_v1.blend. It reopens with the
43.437 object scale intact, no runtime pose constraints, finite meshes, and
3.86e-5 maximum in-memory action replay matrix error. A separate reopened-file
validation checked all 61 frames and passed. The preview still requires visual
review before any further animation work. Inspect frames 0, 15 (maximum glove
gap), 19–20 (maximum pose step), 26 (maximum elbow bend), 45, and 60.
