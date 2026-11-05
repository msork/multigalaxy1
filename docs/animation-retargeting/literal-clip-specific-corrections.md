# Literal clip-specific corrections

## Scope and safety

The shared literal retarget converter and the approved Luigi walk are treated as
immutable. A separate postprocessor accepts only source action names listed in
`tools/blender/profiles/luigi_clip_corrections_v1.json`; it copies the baked
target action and changes quaternion values for the named Waluigi bones only.
The correction is a positive slerp from the Waluigi local rest delta toward the
converted key quaternion. It does not change key times, interpolation, root
translation, bone translations, scales, fists, source assets, or production
objects. Output previews use new filenames.

RUN, JUMP, and SWIM use this path because the exact V2 walk mapping is the
approved reference and must remain bit-for-bit unchanged.

## Diagnostic evidence and corrections

The raw Luigi BCKs were evaluated on disposable source-rig copies and rendered
separately from the Waluigi v1 previews at matching frames in front and side
views. The mapped rest-relative rotation magnitudes agree closely between
source and target (typically within a few thousandths of a radian in the
sampled limbs). This rules against a broad source-axis or rest-basis error.
The visual differences are concentrated in large excursions on Waluigi's
longer, narrower limbs and in how those excursions distribute through his
overlapping arm branches. Corrections therefore reduce only selected target
rotation amplitudes and retain positive direction.

The current factors are deliberately clip-local:

| Clip | Local changes |
| --- | --- |
| `luigi_armature_luigirun` | Leg rotations 0.82–0.88; shoulders 0.90; upper-arm branches 0.84; lower-arm branches 0.80; hands 0.90. This reduces exaggerated stride and elbow roll without changing cadence. |
| `luigi_armature_luigijump` | Spine bones 20/21 and the Waluigi-right arm branches 0.90. Bone 19 has no keyed channel in this source clip and is left untouched. |
| `luigi_armature_swimbreast` | Left shoulder/upper-arm branch 0.75 and left lower-arm branch 0.65. Right side and all other motion stay unchanged. |

For JUMP, the source `luigijump` action is one-shot and ends at frame 24 in an
airborne pose. Its endpoint is closest to `fall` frame 0 among the inspected
separate source clips: mean rest-local quaternion difference 0.330 rad versus
0.468 for `jumpland` and 0.594 for `land`. This is evidence of a likely
airborne-to-fall continuation, not an embedded transition. The converter keeps
frame 24 exactly as the source-authored airborne endpoint and creates no
landing.

## Validation and commands

`apply_literal_clip_corrections.py` samples the resulting evaluated Waluigi
meshes at every quarter frame, checks finite vertices and motion continuity,
preserves object scale 43.437, and writes a new preview plus an expected pose and
mesh hash report. `validate_literal_clip_reopen.py` reopens that preview and
checks all recorded quarter-frame pose and world-vertex hashes, visible body
count, constraints, and scale. `analyze_direct_arm_mesh_continuity.py` can sample
the shoulder, elbow, wrist, and glove weighted-surface seams at those same
quarter frames.

Example:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v1.blend' --python 'tools\blender\apply_literal_clip_corrections.py' -- --output 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v2.blend' --report 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v2_validation.json' --renders 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v2_renders'
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v2.blend' --python 'tools\blender\validate_literal_clip_reopen.py' -- --report 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v2_validation.json' --output 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v2_reopen.json'
```

Reports and `.blend` files are disposable outputs under `tools/blender/reports/`
and are not source assets to commit.
