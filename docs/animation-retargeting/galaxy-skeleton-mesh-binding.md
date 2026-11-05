# Galaxy skeleton driving a Sluggers character mesh

Status: disposable proof of concept; not approved for gameplay or the full animation bank.

## Hypothesis

The video reference (`GKDDP4vZfoc`) suggests that a custom Galaxy character may use Mario's original animation. We treat that as a hypothesis about a possible asset workflow, not as a verified tutorial technique. Existing video research found metadata but no usable transcript or directly reviewable technique. This experiment tests the concrete variant that is technically available here: copy the Galaxy Luigi armature and its original action curves, consolidate the Sluggers Waluigi mesh's weights onto the Galaxy controls, and play the original animation on that rig.

This is distinct from the approved literal converter. The converter writes converted motion onto the Sluggers skeleton; this prototype keeps a Galaxy skeleton and changes only disposable copies of the Waluigi mesh's vertex groups to bind them to it.

## Implementation

- `tools/blender/prototype_galaxy_skeleton_skinbind.py` creates a disposable copy of Luigi's 30-bone armature, copies the selected Blender 5.2 layered action curves and slot to the copy, clones the Waluigi mesh objects/data, and assigns explicit consolidated weights from the profile.
- `tools/blender/profiles/luigi_skeleton_skinbind_waluigi.json` contains the character-specific landmark and group mapping. The prototype keeps original mesh vertices and material slots; only cloned vertex-group assignments and armature modifiers change.
- `tools/blender/validate_galaxy_skeleton_skinbind.py` reopens the saved file and checks rig/action binding, visibility, modifiers, finite evaluated transforms, and evaluated mesh vertices at quarter-frame samples.
- `tools/blender/probe_skeleton_mesh_binding.py` and `tools/blender/diagnose_galaxy_skeleton_skinbind.py` support read-only source inspection and diagnostics.

The rest alignment is a fitted uniform similarity with a fixed Z-up to target-local Y-up axis conversion. The measured scale factor is `0.0299423922` in target armature-local units. It cannot absorb Waluigi's different limb, torso, or hand proportions. The production Waluigi object and its intentional scale (`43.437`) are not modified or applied. The candidate is a separate armature and cloned meshes.

## Tests and results

The prototype was run from disposable copies of the existing approved converter previews. Original source actions were read and copied curve-for-curve; sampled source/copy curve error was zero. Each candidate was saved and reopened, then evaluated every quarter frame. Reopen validation passed in all four files: 30-bone candidate armature, assigned action/slot, two visible bound meshes, only the candidate armature visible, no runtime pose constraints, and no non-finite bone or mesh values.

| Clip | Source action | Samples | Reopened evaluated vertices |
|---|---|---:|---:|
| Walk | `luigi.001_armature_walk` | 241 (0–60) | 727,820 |
| Run | `luigi_armature_luigirun` | 245 (0–61) | 739,900 |
| Jump | `luigi_armature_luigijump` | 97 (0–24) | 292,940 |
| Swim | `luigi_armature_swimbreast` | 237 (0–59) | 715,740 |

Rest mesh rebind reproduced the original copied mesh within `3.25e-5` world units. Rest landmark RMS was `0.245` target-local units (worst mapped landmark `0.511`), so the global fit is approximate, not an exact proportion match.

Compared with the existing V2 preview meshes at matching quarter frames, whole-mesh world-space RMS / p95 / maximum errors were:

| Clip | RMS | p95 | Maximum |
|---|---:|---:|---:|
| Walk | 12.17 | 17.04 | 28.25 (frame 42) |
| Run | 10.46 | 21.19 | 44.61 (frame 55.5) |
| Jump | 8.91 | 19.65 | 44.03 (frame 14.25) |
| Swim | 75.32 | 85.58 | 126.09 (frame 12) |

These are absolute world-space differences and include root/object placement mismatch, so they should not be read as pure deformation error. Swim is especially affected: after subtracting per-frame global translation, its mean shape-only RMS is about `17.54`; the comparable values are about `8.42` walk, `7.33` run, and `8.03` jump. The current V2 path transfers root motion to the armature object, while the direct candidate evaluates the Galaxy hierarchy's root channel. This coordinate-path difference needs a deliberate policy before any meaningful clip-level score comparison.

The arm region and seam diagnostics expose limitations that a whole-mesh score can hide. Consolidating Sluggers' overlapping upper-arm/forearm branches onto one Galaxy bone per segment loses independent deformation. The largest change in the nearest-surface elbow p95 metric was `+4.30` for swim; jump's left wrist/forearm metric changed by as much as `+9.20`. These are proximity diagnostics, not literal open-gap measurements. The hand/finger groups also lack matching Galaxy digit bones: assigning them to the hand control does not preserve the authored closed-fist articulation. Candidate renders show open fingers, unlike the closed fists in the approved previews. Therefore the current profile is not visually approved even though binding and reopen checks pass.

Candidate files and reports are disposable local artifacts and are intentionally not committed:

- `tools/blender/reports/altbind_luigi_walk.blend`
- `tools/blender/reports/altbind_luigi_run.blend`
- `tools/blender/reports/altbind_luigi_jump.blend`
- `tools/blender/reports/altbind_luigi_swim.blend`
- Matching `.json`, `.reopen.json`, and render directories use the same `altbind_luigi_<clip>` prefix.

These previews are technical candidates, not human-approved animation. The golden walk V2 and existing converter/profile were inputs only and were not edited.

## Manual corrections still required

Automatic weight consolidation is insufficient. A usable adapter would need character-specific corrective treatment for:

1. Shoulder and elbow zones, where Sluggers' overlapping branches jointly deform one visible region while Galaxy offers a simpler chain.
2. Wrist/glove transitions, especially the left wrist during jump and both elbows during run/swim.
3. Finger/thumb articulation, because the 30-bone Galaxy skeleton does not provide the Sluggers digit controls. A separate fist pose mechanism or auxiliary digit controls are needed; group collapsing alone opens the fingers.
4. Root translation and object-space policy, so direct Galaxy playback and converter previews are compared in the same coordinate space.
5. Nonuniform proportion differences. One uniform rest fit cannot preserve arm reach, torso width, and hand size simultaneously.

The appropriate next experiment, if pursued, is manual region-specific reweighting/corrective controls on disposable copies, validated by evaluated surface geometry—not broad automatic weight transfer.

## Feasibility and scope

For Waluigi, it is feasible to make the original Galaxy animation curves drive a cloned Galaxy skeleton while a cloned Waluigi mesh follows it. This proof of concept does not establish acceptable deformation. The branch and finger topology mismatch, imperfect rest fit, and root-space difference are meaningful blockers; four sampled clips are not enough to declare the alternative better than the converter.

It could scale operationally across 431 clips once a character adapter is validated: the same copied action curves can be assigned to the same control rig and evaluated at each clip's original timing. That only scales playback, not quality. A successful character-specific weighting/correction profile and regression suite would still be required, with clip categories and exceptional curves reviewed. No full-bank conversion has been run.

For Wario, this architecture is plausible in principle but untested. The available Sluggers Wario rig has 91 bones and a different bilateral branch/group layout. The exact playable Galaxy Mario skeleton/action owner was not established in this experiment; a nearby Mario model is not sufficient evidence of skeleton compatibility. A Mario-to-Wario prototype requires provenance-confirmed Galaxy Mario skeleton and original actions, followed by its own disposable binding and deformation tests.

## Reproduction

Run the builder inside Blender 5.2 background mode, supplying a disposable input scene, source action, and the profile. For example, from the repository root in PowerShell:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  'tools\blender\reports\direct_luigi_waluigi_literal_walk_v2_root_translation.blend' `
  --background --python 'tools\blender\prototype_galaxy_skeleton_skinbind.py' -- `
  --source-rig 'luigi.001_armature' `
  --source-action 'luigi.001_armature_walk' `
  --profile 'tools\blender\profiles\luigi_skeleton_skinbind_waluigi.json' `
  --output 'tools\blender\reports\altbind_luigi_walk.blend' `
  --report 'tools\blender\reports\altbind_luigi_walk.json' `
  --renders 'tools\blender\reports\altbind_luigi_walk_renders' `
  --render-frames 0 15 30 45 60
```

The other actions require their matching disposable V2 input `.blend`, action name, and output/report/render paths. Reopen with `validate_galaxy_skeleton_skinbind.py` and provide `--output` for its JSON report.
