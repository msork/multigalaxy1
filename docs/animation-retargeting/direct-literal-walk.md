# Literal Luigi walk transfer onto Waluigi

## Purpose

This prototype transfers only `luigi.001_armature_walk`, frames 0–60, onto a
disposable copy of the 92-bone Sluggers Waluigi rig. Luigi's original curves
remain the motion source. The adapter performs no IK, trajectory solve,
contact correction, learned pose fitting, or post-bake smoothing.

The failed V4/V5 leg work used geometric axis conversion and a two-bone IK
reconstruction. V5's sideways leg motion showed that numerical joint matching
did not justify changing Luigi's authored movement. This version takes a
hierarchy-to-hierarchy route instead.

## Architecture and transform rule

`BONE_OUTPUTS` explicitly maps every source bone with multi-key walk channels
to one or more Waluigi outputs. Both Waluigi arm branches receive their source
segment's same motion delta, each converted using its own rest frame. Waluigi
fingers and unmapped deform helpers retain the validated closed-fist walk
baseline.

For mapped source joint `s` and target bone `t`, the adapter evaluates the
source hierarchy, removes the source bone's armature-space rest orientation,
converts the resulting delta between object coordinate frames, and applies it
to the target rest orientation:

```
Delta_s = Q_s_pose * inverse(Q_s_rest)
A = inverse(Q_t_object) * Q_s_object
Q_t_pose = A * Delta_s * inverse(A) * Q_t_rest
```

The target pose orientation is then converted by Blender to that target bone's
local basis after its target parent has been evaluated. This matters for
Waluigi's extra helper bones and parallel arm branches: each branch gets the
source segment's absolute rest-relative orientation once, rather than
accumulating a copied rotation down multiple target bones. Animated local
translation is converted through the corresponding rest frame and scaled by
mapped bone-length ratio; root translation uses the rest-height ratio. Object
scale is excluded from orientation calibration and the intentional Waluigi
scale `43.437` is preserved. The baseline action is copied to a new action;
only explicitly mapped Waluigi channels are replaced.

Blender 5.2 layered source FCurves are evaluated directly from Luigi's selected
action slot. Every source key time is retained, and the transformed pose is
sampled every 0.25 frames over 0–60. Because quaternion basis conversion is
nonlinear, the baked target is a sampled approximation between those points;
the report records the source keys, sample spacing, and reopened pose error.

## Safety

- The input workfile, source Luigi action, production Waluigi rig, and existing
  baseline action are read only.
- The output rig, armature data, and two skinned preview meshes are duplicates.
- Waluigi object transforms and `43.437` scale are preserved; never apply them.
- A new action name and new preview path are mandatory. Existing previews and
  the untracked user report files are left untouched.
- No runtime constraints remain on the preview target.
- The preview has one visible Waluigi character; source/reference objects are
  hidden. Automated renders are not human visual approval.

## Validation

The runner checks complete source keyed-bone coverage, mapped bone existence,
source key retention, output finite transforms, unchanged object scale,
fixed approved finger poses, action replay at every baked sample, and absence of
runtime constraints. It also records armature-modified world-space mesh vertex
change versus the baseline at frames 0, 15, 30, 45, and 60. Front and side
renders at those frames are generated for human review. The report does not
claim that a numerically passing preview is visually approved.

## Run

From the repository root in PowerShell:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend' --python 'tools\blender\prototype_direct_luigi_walk_literal.py' -- --preview 'tools\blender\reports\direct_luigi_waluigi_literal_walk_v1.blend' --report 'tools\blender\reports\direct_luigi_waluigi_literal_walk_v1.json' --renders 'tools\blender\reports\direct_luigi_waluigi_literal_walk_v1_renders'
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\direct_luigi_waluigi_literal_walk_v1.blend' --python 'tools\blender\validate_direct_literal_walk.py' -- --fit-report 'tools\blender\reports\direct_luigi_waluigi_literal_walk_v1.json' --output 'tools\blender\reports\direct_luigi_waluigi_literal_walk_v1_reopen.json'
```

Run math tests with Blender 5.2.3:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python 'tools\blender\test_literal_retarget_math.py'
```
