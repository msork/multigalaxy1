# Hybrid Galaxy control / Waluigi deformation experiment

Status: exploratory prototype; **not approved for animation use**.

## Purpose and isolation

This experiment tests whether Luigi's original 30-bone action can remain the
animation source while Waluigi's 92-bone skeleton, skin weights, mesh, and
closed-fist hand pose provide the deformation rig. It is separate from the
literal converter and its approved golden WALK V2. The builder opens a
disposable input `.blend`, writes a new preview and JSON report, and never saves
over source assets, actions, or the golden file. Generated `.blend`, JSON, and
render files are reports/artifacts and are not committed.

## Adapter layout

- The `HybridGalaxy_Luigi_Control` copy has the original Luigi bone hierarchy.
  Its dedicated Blender 5.2 action slot gets a curve-for-curve copy of the
  selected source action. The source action is not edited.
- `HybridWaluigi_Deform_Rig` retains the full target hierarchy. Two disposable
  mesh copies preserve the input mesh data, materials, vertex-group names and
  per-vertex weights. There are no runtime constraints in the saved target.
- A character profile holds semantic bone correspondences, landmarks, arm
  branch controls, finger bones, region groups and seam pairs. The converter
  handles action slots, sampling, rest-axis conversion, baking and validation.
- For `local_delta`, each source local pose rotation is converted as
  `q_target_delta = C * q_source_delta * inverse(C)`, where `C` is the
  rotation component of the rest-space axis calibration for that bone pair.
  The target's independent Waluigi branches receive their corresponding
  semantic source deltas. This preserves the target skeleton and weights, but
  does not infer the native relationship between those branches.
- The tested previews use `body_context=v2_reference`: non-arm body context is
  sampled from the V2 reference, while mapped arm controls are overridden by
  converted Luigi motion. Root translation is carried separately. Finger and
  thumb pose bases are fixed to the validated V2 fist state.

This is a hybrid *driver/deformer* experiment, not a weight transfer: Waluigi's
weights remain on Waluigi's armature. It does not provide separate new control
degrees of freedom for every deform branch; it applies calibrated local deltas
to selected existing target bones.

## Reproduction

From the repository root, run Blender 5.2 with one of the known disposable
source files and an action name. For example:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  'tools\blender\reports\direct_luigi_waluigi_literal_walk_v2_root_translation.blend' `
  --background --python 'tools\blender\prototype_hybrid_skeleton_adapter.py' -- `
  --source-action 'luigi.001_armature_walk' `
  --output 'tools\blender\reports\hybrid_luigi_walk.blend' `
  --report 'tools\blender\reports\hybrid_luigi_walk.json' `
  --renders 'tools\blender\reports\hybrid_luigi_walk_renders' `
  --render-frames 0 15 30 45 60
```

Run `validate_hybrid_skeleton_adapter.py` in background mode with the preview
as the startup file and `--output <preview>.reopen.json` to check the saved
action, weights, fist pose, visibility, evaluated mesh, and constraints after
reopen. Use the matching `.blend` that contains the requested action; the walk
input above is not the run/jump/swim source.

## Test results

Each clip was sampled at quarter-frame increments. Reopen validation passed on
all 4 saved previews: the control rig has 30 bones, target rig 92; copied
control-action curves evaluate with maximum error 0; Waluigi vertex-group
membership/weights match the preview source on both meshes; 42 digit bones
remain at the fixed fist basis; target has no runtime constraints; no
non-finite evaluated transforms/vertices were found.

Mesh error compares the candidate and V2 reference evaluated vertices after
subtracting each mesh's armature-root position in armature space. This removes
object/root translation from the shape RMS; it does not hide local deformation
differences. Values are in the report's Waluigi armature units.

| Clip | Samples | Root-normalized vertex RMS | P95 | Maximum | Largest arm-seam delta |
|---|---:|---:|---:|---:|---:|
| Walk | 241 (0–60) | 0.5430 | 1.4317 | 2.6121 | 0.0777, left wrist/glove, frame 37 |
| Run | 245 (0–61) | 0.4243 | 1.1561 | 2.6001 | 0.4714, left forearm/wrist, frame 9 |
| Jump | 97 (0–24) | 0.5358 | 1.5480 | 2.0684 | 0.1035, right forearm/wrist, frame 0 |
| Swim | 237 (0–59) | 0.6366 | 1.5233 | 3.2556 | 0.3321, left forearm/wrist, frame 12.75 |

The seam metric is the absolute difference between candidate and V2 reference
95th-percentile nearest-surface distances for the named weighted-group seam.
It is a geometric proxy, not a guarantee against visible cracks or overlaps.
The maximum vertex deviations are too large to claim mesh equivalence. Wrist
orientation also remains substantially different: maximum root-relative hand
rotation differences (`bone_30` / `bone_65`) were 177.0° / 57.5° walk,
166.3° / 92.7° run, 125.2° / 105.5° jump, and 159.7° / 158.3° swim.

## Visual and technical limitations

Front and side renders were produced for each clip. Inspection of the walk
frame-15 renders shows the front view upright but the supposed side view rolled
onto its side; the matching golden V2 side render is upright. That makes the
hybrid side render unsuitable for comparing silhouettes until the render/view
orientation discrepancy is diagnosed. It is not human approval. The front
render also visibly differs in arm pose from the golden walk. Numerical
root-normalized comparisons independently show nontrivial deformation error,
especially at left forearm/wrist during run and swim. The preview is therefore
only useful for diagnosing the adapter, not sign-off.

Other limits:

- Independent Waluigi branch motion is not preserved when branches share a
  single Luigi semantic driver; independent branch rotation/roll is lost.
- Static fist locking preserves the tested hand pose, but prevents legitimate
  hand articulation in other clips and can leave wrist-to-glove orientation
  mismatched.
- The profile is Waluigi-specific. The conversion and validation code is
  reusable in shape, but Mario→Wario needs a separately measured profile,
  rest calibration, branch mapping and hand pose.
- This test covers only four actions. It says nothing about all 431 clips or
  action events, additive layers, root-motion policies, or gameplay timing.
- The auxiliary `absolute_rest_delta` mode was retained as a diagnostic
  alternative; `local_delta` is the mode used for the results above.

Conclusion: action preservation and weight/fist invariants work in this
prototype, but evaluated-mesh and view/silhouette checks fail the quality gate.
Do not replace the literal converter or expand to the full bank based on these
results.
