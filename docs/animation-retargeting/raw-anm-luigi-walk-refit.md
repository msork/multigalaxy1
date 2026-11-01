# Luigi walk refit against raw native Waluigi calibration

## Status

The new raw-ANM target model passes identity replay and 27 action-level PCA
holdouts. The Luigi walk solver was rerun with the same validated v7 full-body
baseline and generated a disposable preview, but the semantic refit is **not
accepted**: fresh-process evaluation of the saved action differs from the fit
report by up to 0.467 RMSE (0.461 on the right). Do not start other Luigi
animations or treat the saved residuals as an improvement claim.

## Model and preview

The raw model contains 38 context bones (228 pose features) and uses 224 PCA
latent dimensions. Training uses 118 actions / 4,116 samples; 27 complete
actions / 1,168 samples are held out. The former inferred `.004` dataset is
comparison-only and the training loader rejects it.

The diagnostic preview is
`tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_raw_anm_v4.blend`
(local, ignored). Rig: `SemanticWalk_Waluigi_RawANM_v1_Target_Rig`. Action:
`Waluigi_LuigiWalk_SemanticBothArms_RawANM_v1`. The 61-frame baked action has
zero runtime constraints, preserves scale and baseline context, and contains
the complete textured Waluigi meshes. These checks do not repair the semantic
consistency failure.

## Saved-evaluation residuals

| Feature | Left | Right |
|---|---:|---:|
| Shoulder rotation X/Y/Z (rad) | 0.722 / 0.210 / 0.465 | 0.959 / 0.239 / 0.725 |
| Elbow flexion (rad) | 0.220 | 0.191 |
| Forearm roll (rad) | 1.279 | 1.252 |
| Wrist rotation X/Y/Z (rad) | 0.212 / 0.092 / 0.098 | 0.148 / 0.115 / 0.137 |
| Hand displacement X/Y/Z | 0.184 / 0.253 / 0.222 | 0.291 / 0.302 / 0.766 |

Relative to v7's saved evaluation, results are mixed: left elbow RMSE changes
from 0.233 to 0.220 rad, while left forearm-roll RMSE changes from 1.085 to
1.279 rad. Right elbow changes from 0.270 to 0.191 rad, right forearm roll
from 1.409 to 1.252 rad, and right hand Z from 0.552 to 0.766. Because the
fit-stage and saved-action measurements disagree, these cross-model deltas are
descriptive only.

The saved target elbow curves show abrupt transitions near frames 5–8,
30–35, 40–44 and 50–52. Inspect those alongside frames 0, 15, 23, 31, 36, 43,
and 60 if opening the diagnostic file. Do not visually approve it until the
saved-vs-fit mismatch is understood and corrected.

Summary values are recorded in
[`raw-anm-context-model-results.json`](raw-anm-context-model-results.json).
