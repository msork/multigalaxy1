# Hybrid adapter vs. literal converter: diagnostic comparison

Status: diagnostics only. No animation curves, actions, rigs, source assets, or
converter code were modified. The only committed changes for this check are the
render/measurement scripts and this report.

## Camera and normalization correction

The previous comparison renderer called `to_track_quat('-Z', 'Z')` for Blender
cameras. A camera looks along local `-Z` and its image-up axis is local `+Y`;
using local `Z` as the up axis rolled horizontal side views. The diagnostic
renderer now calls `to_track_quat('-Z', 'Y')` and uses fixed world `+Z` as up.

Each evaluated mesh is transformed by the inverse Waluigi armature object
matrix, removing object translation, rotation, and the intentional 43.437
scale. A rest-calibrated basis from pelvis/head landmarks sets one canonical
character-facing direction. All clips use the same 512×512 transparent render,
5.0-unit orthographic scale, camera locations, and 720 W area light. The
neutral row disables the target action and resets pose bases to rest. Both
pipelines render identically at rest (vertex RMS 0 and silhouette IoU 1.0 in
all views), confirming the normalization and framing agree.

Mesh region RMS, elbow landmark offsets, wrist-to-forearm relative rotation,
distinct weighted-group seam distances, and alpha silhouette IoU are reported
per sample in `contact_sheets/comparison.json`. The seam calculation excludes
shared vertex IDs. This matters because all 54 `bone_27` wrist vertices are
also in `bone_30`; the earlier unfiltered nearest-surface check therefore
reported a meaningless zero gap. The diagnostic instead measures distinct
`bone_29` forearm vertices against `bone_30` glove vertices (and homologous
right-side groups). These point-cloud distances are seam proxies, not a
substitute for topology or human inspection.

## Inputs and synchronized samples

| Clip | Reference | Hybrid | Samples |
|---|---|---|---|
| Walk | `direct_luigi_waluigi_literal_walk_v2_root_translation.blend` (golden V2) | `hybrid_luigi_walk.blend` | 0, 15, 30, 45, 60 |
| Run | `luigi_armature_luigirun_waluigi_anatomical_v4.blend` | `hybrid_luigi_run.blend` | 5, 8, 11, 14, 24, 28, 32, 36, 40, 44, 46 |
| Jump | `luigi_armature_luigijump_waluigi_anatomical_v4.blend` | `hybrid_luigi_jump.blend` | 9, 12, 13, 14, 15, 18, 21, 24 |
| Swim | `luigi_armature_swimbreast_waluigi_anatomical_v4.blend` | `hybrid_luigi_swim.blend` | 5, 11, 12, 13, 14 |

Each contact sheet uses one row per sample, first row `REST`, then the listed
times. Columns are reference on the left and hybrid on the right. Each sheet
uses the view in its filename.

### Contact sheets

- Walk: [front](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/walk_front_contact.png), [side](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/walk_side_contact.png), [3/4](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/walk_three_quarter_contact.png)
- Run: [front](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/run_front_contact.png), [side](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/run_side_contact.png), [3/4](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/run_three_quarter_contact.png)
- Jump: [front](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/jump_front_contact.png), [side](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/jump_side_contact.png), [3/4](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/jump_three_quarter_contact.png)
- Swim: [front](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/swim_front_contact.png), [side](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/swim_side_contact.png), [3/4](../../tools/blender/reports/hybrid_literal_comparison/contact_sheets/swim_three_quarter_contact.png)

Individual normalized geometry bundles and PNG renders are under
`tools/blender/reports/hybrid_literal_comparison/<clip>/<candidate>/`.

## Findings

The adapter preserves the neutral Waluigi shape, but its animated arm pose is
far from the matching literal V4 pose. The measurements below describe this
candidate-to-reference difference; they are not a direct score against Luigi's
anatomical target. Region RMS uses armature units after removing object scale.

| Defect under review | Measured difference over requested frames | Assessment |
|---|---|---|
| Run left-arm backward swing (5–14, 24–46) | Left elbow head moves 0.57–0.77 units from V4. Mean left forearm and glove vertex RMS are 0.96 and 1.09. Left-hand relative rotation differs by 46.7° vs. `bone_27` on average and 104.2° vs. `bone_29` (maximum 177.0°). Side silhouette IoU averages 0.489. Distinct forearm-to-glove seam distance changes by +0.146 on average, reaching +0.458. | **Worse as a replacement; no reliable correction demonstrated.** The hybrid visibly changes the arm path and hand orientation, rather than preserving the run trajectory while correcting the backward reach. Seam separation also increases on several target frames. |
| Jump left-wrist twist (9–24) | Left forearm RMS is 1.48 and glove RMS 1.90. Wrist-relative orientation differs by 78° on average (max 117.7°). Elbow head shifts by up to 1.01 units. Silhouette IoU averages 0.49 side / 0.55 front. | **Worse overall; wrist-only effect inconclusive.** The hybrid hand looks less like the same raised-arm pose, so any apparent reduction in the twist is confounded by losing the arm trajectory. It is not evidence that wrist deformation is fixed. |
| Swim left elbow/forearm (5, 11–14) | Left elbow head shifts 0.95–1.16 units. Forearm RMS averages 1.35; wrist-relative orientation differs by 70° vs. `bone_27` and 106° vs. `bone_29` on average. Side silhouette IoU averages 0.378. Distinct forearm-to-glove distance worsens by +0.079 on average and +0.296 at frame 13. | **Worse.** The hybrid changes the elbow/forearm pose substantially and does not provide a stable improvement to the downward forearm or elbow placement. |
| Walk vs. golden V2 | At frame 15, forearm RMS is 1.53 and glove RMS 2.08; elbow landmark offset is 0.92. Side silhouette IoU is 0.60. Across the five sampled poses, average forearm/glove RMS is 1.13/1.65. | **Worse as a golden-walk replacement.** Rest matches exactly, but the animated arms diverge materially from the approved walk. |

Across all clips, the hybrid duplicates the Waluigi rest pose but does not
preserve the literal converter's animated shoulder/elbow/wrist trajectory.
Large wrist orientation and elbow-position differences make the hybrid's lower
or differently directed hands an invalid basis for declaring the listed
defects corrected. This experiment does not support adopting the hybrid
adapter for these clips.

## Reproduction

The Blender extraction command pattern is:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' '<preview.blend>' --background `
  --python 'tools\blender\diagnose_hybrid_literal_comparison.py' -- `
  --clip run --candidate hybrid --output '<candidate>\data.json.gz' `
  --render-dir '<candidate>\renders' --frames 5 8 11 14 24 28 32 36 40 44 46 --neutral
```

The standard-library assembler builds the contact sheets and quantitative
report:

```powershell
python tools/blender/assemble_hybrid_literal_diagnostics.py `
  --root tools/blender/reports/hybrid_literal_comparison `
  --output tools/blender/reports/hybrid_literal_comparison/contact_sheets
```
