# Direct Galaxy walk contact and fist pass (v4)

## Scope

This pass keeps the v3 geometric shoulder/elbow/wrist solver and its shared
branch distribution. It changes only the Luigi walk (frames 0–60) on a
disposable Waluigi duplicate. The production rig, the approved leg action, and
source assets remain untouched. The duplicate retains Waluigi's object scale
of 43.437 and original bound meshes/materials.

## Findings and changes

The v3 legs replayed `Waluigi_GalaxyWalk_Legs_Baked` without transferring the
source pelvis bob or planting feet. Luigi's pelvis height varied 4.57 source
units, while the target pelvis landmark was constant. Weighted target foot
vertices drifted by as much as 8.11 world units during source stance. Stance
segments from the source curves are left frames 29–34 and right frames 59–3
(cyclic).

The new walk layer maps Luigi's pelvis bob through the Waluigi object frame and
leg-length ratio. It solves both leg segments with a two-bone geometric knee
pole, anchors the ankle and foot orientation during stance, and follows Luigi's
foot path during swing. This changes only bones 6–11 and 13–17. Evaluated
weighted foot surfaces now slide at most 0.159 world units on the left and
0.088 on the right. Stance clearance is 0.00026–0.00029 on the left and
−0.00030–0.314 on the right, relative to the scene floor at world Z=0.

The arms remain driven by the v3 torso-relative geometric solve and the v2
distribution across every deform branch. A dimensionless lateral compression
reduces outward reach while preserving the source bend plane and axial roll.
Measured lateral excursion is 85.7% of Luigi on the left and 84.0% on the
right. Forward/back excursion retention is 114.0% and 115.2%; elbow flexion
RMSE is below 1.4e-6 radians. Forearm roll motion RMSE is 0.0291 radians left
and 0.0515 right. Maximum pose step over quarter-frame samples is 0.1283
radians.

The walk baseline's finger pose was extended. The left fist now uses the
user-validated closed-fist pose from the v5 left-arm preview. The right fist is
derived by reflecting that local rotation delta through corresponding
production rest frames (`bone_31..bone_51` to `bone_66..bone_86`). Both hands
are keyed as a fixed local pose for the entire walk. The closed-fist reference
quaternions are stored in
[`waluigi_v5_closed_fist.json`](../../tools/blender/profiles/waluigi_v5_closed_fist.json).
Weighted distal-finger-to-hand centroid distance falls by 42.7% on the left
and 42.6% on the right, and varies by less than 0.006 world units over the
walk. The right pose is a rest-calibrated mirror of the visually validated
left pose; it has not yet received independent visual sign-off.

The Galaxy action contains Euler channels that wrap near frame 28→29. Direct
component interpolation created a transient half-turn at frame 28.5 even though
the endpoint quaternions are equivalent. The source sampler now uses shortest
quaternion interpolation only for such wrapped intervals, leaving the authored
interpolation unchanged elsewhere. The quarter-frame bake has no large jumps.

## Saved and reopened validation

The prototype writes a fresh action named
`Waluigi_DirectGalaxyWalk_v4_ContactNarrowFists`; it does not overwrite
`Waluigi_GalaxyWalk_Legs_Baked`. It has no runtime constraints. The same two
Waluigi meshes remain bound to the duplicate armature, their original materials
and packed images are present, and no second character is visible in the
preview.

The builder passes all numeric gates. Reopening the saved file and checking all
241 samples at 0.25-frame spacing reproduces pose matrices within
1.91e-6 maximum element error. Weighted foot-surface samples reproduce exactly;
the reopened fist metric differs from the saved reference by at most 0.00694
world units. Scale remains 43.437 and the constraint count is zero.

Preview: `tools/blender/reports/direct_luigi_walk_v4_contact.blend`.
Reopen diagnostics: `tools/blender/reports/direct_luigi_walk_v4_reopen.json`.
Builder: `tools/blender/prototype_direct_luigi_walk_contact.py`.
Reopen validator: `tools/blender/validate_direct_luigi_walk_contact.py`.

Rebuild and reopen-check with Blender 5.2.3 from the repository root:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background tools\blender\reports\direct_luigi_walk_v3_trajectory.blend --python tools\blender\prototype_direct_luigi_walk_contact.py -- --report tools\blender\reports\direct_luigi_walk_v4_contact.json --preview tools\blender\reports\direct_luigi_walk_v4_contact.blend
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background tools\blender\reports\direct_luigi_walk_v4_contact.blend --python tools\blender\validate_direct_luigi_walk_contact.py -- --report tools\blender\reports\direct_luigi_walk_v4_contact.json --output tools\blender\reports\direct_luigi_walk_v4_reopen.json
```

## Visual review still required

Inspect frames 0, 15, 30, 45, and 60, plus left stance frames 29–34, right
stance frames 0–3 and 59–60, and subframes 28.25/28.5/28.75. Check natural
weight transfer, knee shape, shoe-floor contact, arm width relative to the
torso, elbow bend, forearm roll, glove attachment, and that both mirrored fists
look naturally closed. Numerical gates are not a substitute for this review.
