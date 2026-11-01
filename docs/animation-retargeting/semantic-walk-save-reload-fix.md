# Semantic walk save/reload pose mismatch

## Root cause

The fitter duplicated `Waluigi_Arm_Clean_Backup`, which is hidden in the
recovery workfile. The duplicate inherited the hidden object and view-layer
states. The fitter only cleared those states near file save, after pose
sampling and writes. Blender's dependency graph evaluated that hidden rig
with stale/incomplete pose matrices. The fit report therefore measured a
different evaluated pose from the requested PCA pose that was converted to
action channels. In v5 this reached `2.40846` matrix units at `bone_77`, frame
44; the saved action itself consistently replayed its own channels.

An isolated duplicate test confirmed the cause: pose requests on the hidden
clean-rig duplicate differed by up to `2.40846`; clearing `hide_viewport`,
`hide_render`, and `hide_set` before evaluation reduced the error below
`5e-7`. Quaternion sign continuity, action slot binding, keyed frame coverage,
and interpolation were not the cause.

## Fix and validation

The fitter now makes the disposable target visible immediately after
duplication and before sampling or writing any pose. It applies armature-space
pose matrices parent-first, flushes Blender's dependency graph between
hierarchy levels, and derives baked channels from the evaluated fit pose. It
records the per-frame fitted pose and local-basis matrices in its ignored
diagnostic report. The fresh-process validator compares both those references
and the requested matrices against the reopened action over every bone at
frames 0–60.

The final disposable preview is
`tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v9.blend`.
Its rig is `SemanticWalk_Waluigi_RawANM_v6_Target_Rig`; its action is
`Waluigi_LuigiWalk_SemanticBothArms_RawANM_v6`. It contains both complete
textured Waluigi meshes, preserves the intentional 43.437 object scale, and
has zero runtime constraints. Production objects/actions and the accepted raw
ANM calibration/PCA pipeline were not changed.

Fresh-process save/reload comparison:

- Maximum fitted/requested armature-space pose matrix error: `3.5763e-6`,
  `bone_50`, frame `45`.
- Maximum local `matrix_basis` error: `5.0664e-7`, `bone_65`, frame `46`.
- Maximum local-rest reconstruction error: `6.1333e-7`.
- Saved semantic RMSE delta from fit report: left `4.21e-7`, right `1.86e-7`.
- The first pose difference above `1e-5`: none.
- Action slot resolves to the duplicate rig; 920 FCurves cover all 61 integer
  frames with linear interpolation; normalized quaternion sign flips: zero.
- Saved-action pose/semantic comparison gate: **pass**.

The solver's fitted elbow curve still has abrupt transitions before baking;
the largest are `0.7603` rad from frame 41→42 on the left and `0.4991` rad from
frame 50→51 on the right. The reopened action reproduces these fitted matrices
exactly, so they originate in the current per-frame semantic/latent solution,
not baking, interpolation, or frame sampling. No semantic weights were
changed. The broader fit gate also remains failed because a left overlap
branch joint-gap excursion reaches `0.0279` armature units (limit `0.02`). The
preview is numerically faithful to the fitted solution but still needs a
separate semantic continuity correction before visual acceptance.

Reopen validation command:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --disable-autoexec tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v9.blend --python tools/blender/validate_semantic_walk_preview.py -- --dataset tools/blender/reports/native_waluigi_raw_anm_calibration.json.gz --fit-report tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v9_fit.json --report tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v9_saved_validation.json
```
