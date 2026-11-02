# Direct Galaxy animation adapter prototype

## Objective and scope

This prototype tests whether Luigi's authored Galaxy walk curves can drive a
Waluigi-specific control rig and then bake onto the 92-bone Waluigi deform rig.
It is a character-adapter path, not a pose-learning path. The same interface is
intended to support Mario-to-Wario by swapping source, target, and mapping
profiles. It is limited to Luigi walk frames 0-60. It does not batch-convert
animations or modify source assets, production rigs, or approved actions.

## Architecture

`direct_galaxy_adapter.py` owns Blender-side mapping/profile validation and the
rest-calibrated rotation transform. A character adapter records source
semantic/control bones, target control bones, target deform outputs, rest
signatures, scale policy, and per-output correction. The source action and slot
remain read-only. In the current validation workfile, Blender 5.2.3 reports the
walk action as assigned but does not evaluate its pose beyond frame zero in
background mode; copying it into a new object-target slot also failed to
evaluate. The prototype therefore samples the selected original channelbag's
FCurves at frames 0-60 and applies those values to a disposable Luigi control
clone. This preserves source timing at the sampled frame rate while avoiding
guessed action-owner rebinding. Source joint rotations are converted through
the source and target rest frames, composed on Waluigi-proportion virtual
controls, then distributed to the target deform branches. The output action is
baked onto a disposable 92-bone duplicate and has no runtime constraints.

The prototype maps only the bilateral arm branches through a virtual
Waluigi-proportioned control chain. One semantic control transform drives every
deform branch for that segment. Legs, torso, root, head/cap, and fingers stay on
the approved Waluigi walk baseline. Fingers retain the baseline closed-fist
transforms.

## Safety and artifact policy

- Open a saved validation workfile read-only and write a new preview file.
- Duplicate the baseline armature, its armature data, and only the explicitly
  named bound meshes. Never change `123475520_waluigi.gpl` or apply its 43.437
  object scale.
- Preserve source action/slot/channelbags; source is evaluated only on a
  disposable control-rig duplicate.
- Use a unique action name. Never replace `Waluigi_GalaxyWalk_Legs_Baked`.
- Keep constraints off the final baked target. Reject non-finite matrices,
  changed root scale, modified baseline bones, mesh-space elbow/wrist seam
  gaps beyond the authored scale-aware tolerance, opened fists, or
  discontinuous output.
- Save diagnostics separately from the preview. Never save over the input
  workfile or any previously approved preview.

## Retarget sequence

1. Fingerprint source/target object transforms, armature rest data, source
   action slot, mesh binding, and the approved walk baseline.
2. Clone the source rig and retain the complete original action/slot on it.
3. Clone the Waluigi baseline rig and bound meshes. Leave non-arm baseline
   channels intact.
4. At every integer frame 0-60, evaluate the original source curves on the
   control clone and compute its pose matrices from rest and local basis data.
   Conjugate each source local channel delta through source and target rest
   frames. Compose a Waluigi-proportion shoulder → upper arm → forearm → hand
   chain. Distribute each segment as `P_control * inverse(R_control_rest) *
   R_output_rest` to every overlapping deform output. This preserves target
   rest offsets and drives structural bones with the weighted branches.
5. Bake the composed target transforms into a new action and remove temporary
   constraints/helpers from the final duplicate.
6. Reopen the output and compare target matrices, non-arm baseline transforms,
   mesh binding, scale, glove/wrist attachment, hand state, and frame-to-frame
   motion against the in-memory result and approved baseline.

The transform relation is a rest-calibrated orientation transfer, not a
translation copy. Object scale is excluded from the orientation conversion;
this preserves Waluigi's authored proportions and avoids applying 43.437 twice.

## Validation and rollout gates

Numerical gates cover unchanged scale and non-arm matrices, finite values, no
runtime constraints, reopened action reproduction, branch-relative angular
motion, actual armature-evaluated mesh vertices, elbow/wrist surface gaps and
cross-weight edge lengths, closed fist pose, and frame-to-frame jumps. The
candidate compares actual mesh seams to the approved walk baseline in
armature-local coordinates; the 0.02 armature-unit tolerance is converted to
world units using the intentional object scale. A candidate is saved for visual
review only when these checks pass. Visual review remains required for shoulder,
elbow, forearm roll, wrist/glove seam, fists, cap, and walk-cycle transitions.
No right-arm/full-body promotion or new animation is permitted until the walk
candidate is accepted.

## Feasibility evidence and current blocker

The saved `Waluigi_Sluggers.blend` and
`tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend`
provide a 30-bone Luigi source and a 92-bone Waluigi rig in the same Blender
5.2.3 environment. The validation scene retains the original `luigi.001_armature_walk`
action and `Waluigi_GalaxyWalk_Legs_Baked` baseline. Luigi's original action can
therefore remain the source of timing and motion curves.

The mapping is not one-to-one. Waluigi's arms contain overlapping deformation
branches (`22-23-24-26-27-30` and `22-25-29`), with unweighted bridge bones and
weighted glove/forearm bones. Native motion showed that branch pairs `23/25`
and `26/29` change relative orientation; one shared rigid control per pair is
not sufficient. A source-to-deform mapping must therefore keep branch outputs
independent and add corrective controls where a single Galaxy joint feeds
multiple weighted regions. The prototype measures whether rest-calibrated
direct control is enough; a failed deformation gate blocks this architecture
from further rollout rather than triggering model fitting.

The baseline file does not include a live Blender session/bridge dependency.
The prototype can be run in Blender background mode against the saved,
texture-repaired validation scene and writes only to a new report/preview path.

## First walk probe

The 0-60 probe was run against
`tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend`.
The source action has 270 curves in slot `OBluigi.001_armature`. Its arm curves
are dynamic (for example, left/right upper-arm rest-relative orientation
excursion is about 1.02/1.05 radians). The target duplicate is based on
`Waluigi_Arm_Test` and the unchanged
`Waluigi_GalaxyWalk_Legs_Baked` action. Bilateral mapping used:

| Luigi source control | Waluigi deform outputs |
| --- | --- |
| `ShoulderL` | `bone_22`, `bone_23` |
| `ArmL1` | `bone_25`, `bone_26` |
| `ArmL2` | `bone_27`, `bone_29` |
| `HandL` | `bone_30` |
| `ShoulderR` | `bone_57`, `bone_58` |
| `ArmR1` | `bone_60`, `bone_61` |
| `ArmR2` | `bone_62`, `bone_64` |
| `HandR` | `bone_65` |

The baked preview has zero runtime constraints. Reopened pose matrices match the
in-memory target within `1.02e-6` maximum matrix element error. The maximum
frame-to-frame mapped rotation step is `0.0935 rad` at frame 46 on `bone_30`;
armature-space wrist/glove joint gaps are under `8.0e-7` units on both sides.
Non-arm local pose channels and all finger channels match the walk baseline
exactly; object scale remains `43.437`. Relative orientation changes across the
upper-arm branch pairs reach `1.023 rad` (58.6 degrees), and forearm pairs reach
`0.635 rad` (36.4 degrees). This demonstrates independent motion but exceeds
the relative-motion envelope measured in the sampled native throw, so branch
deformation is a key visual risk. Reopen validation confirms exactly two
visible target mesh objects, both bound to the target duplicate, with no other
visible character meshes. Zero-weight bridge bones `bone_24` and `bone_59` are
left on the baseline transform; their descendants carry the mapped motion.

These checks prove the adapter preserves the sampled pose and baseline; they do
not prove that the direct branch mapping looks anatomically natural. The saved
file is a visual-review candidate only. It does not yet add corrective bones or
a Waluigi-shaped 30-bone intermediate control skeleton; the current outputs are
baked directly onto the deform rig. The integer-frame bake does not preserve
arbitrary subframe interpolation exactly. Inspect frames 0, 15, 30, 45, and 60
before extending this approach or building the analogous Mario-to-Wario
profile.

## Mesh continuity correction (current result)

The first direct-per-bone candidate failed visually because it applied Galaxy
rotations independently to deform bones sharing weighted regions. This gave
Waluigi's overlapping branches inconsistent transforms; the largest measured
forearm-to-glove gap reached about 0.20 armature-local units against 0.039 in
the approved walk baseline.

Mesh weights confirmed the actual deformation set. Left bones `23/25/29/27/30`
influence 49/29/59/54/104 vertices; right homologs `58/60/64/62/65` have the
same counts. The glove shells are separate connected mesh components weighted
across wrist and hand bones, while forearm weights belong to the main body
component. Mesh checks now compare evaluated vertices in each bound armature's
local coordinates, eliminating the previous 43.437 object-transform mismatch.

The corrected action `Waluigi_DirectGalaxyWalk_ArmAdapter_v2_ControlChain`
composes shoulder → upper arm → forearm → hand using Waluigi rest offsets, then
distributes each segment transform to all overlapping outputs:

| Control | Waluigi output bones |
| --- | --- |
| Left shoulder | `22` |
| Left upper arm | `23, 24, 25` |
| Left forearm | `26, 27, 29` |
| Left hand | `30` |
| Right shoulder | `57` |
| Right upper arm | `58, 59, 60` |
| Right forearm | `61, 62, 64` |
| Right hand | `65` |

Armature-local evaluated mesh gaps at frames 0/15/30/45/60:

| Seam | Failed v1 | Corrected v2 | Walk baseline |
| --- | ---: | ---: | ---: |
| Left elbow | 0.0239–0.0351 | 0.0276–0.0337 | 0.0276 |
| Left forearm/glove | 0.1570–0.1993 | 0.0355–0.0399 | 0.0393 |
| Right elbow | 0.0239–0.0352 | 0.0277–0.0336 | 0.0277 |
| Right forearm/glove | 0.1571–0.2003 | 0.0369–0.0393 | 0.0391 |

Across all frames, maximum seam surface-gap deviation from baseline is 0.00735
armature-local units (0.319 world units), below the 0.02 local tolerance.
Cross-weight edge p95 relative change stays below 20%. Maximum angular frame
step is 0.09437 rad. Non-arm and fist channels remain unchanged, object scale
remains 43.437, and the baked rig has zero constraints. Reopened pose matrices
match within 1.67e-6 maximum element error. Reopen checks find one visible
armature and two visible meshes bound to it.

Preview: `tools/blender/reports/direct_luigi_walk_meshfixed.blend`. Inspect
frames 0, 15, 30, 45, and 60. This remains a walk-only prototype; other Luigi
animations, subframe interpolation, and Mario-to-Wario require separate tests.
