# Luigi Walk to Waluigi Left Arm: Prototype Run

## Input and output

- Source owner: `luigi.001_armature`, with action
  `luigi.001_armature_walk`, slot `OBluigi.001_armature`, frames 0â€“60.
- The source owner and `Luigi_Animation_Reference` have identical rest
  matrices and object transforms in the recovery autosave. The action owner was
  used directly for this run.
- Target model: 38-bone production-basis PCA/context dataset, 23 training
  actions (845 samples), five holdouts reserved for validation.
- Target walk basis: `Waluigi_GalaxyWalk_Legs_Baked` from the recovery autosave.
- Preview output is a disposable duplicate with only the two production
  submeshes. The intentional 43.437 rig scale remains represented by its root
  transform; no production object/action was written.

## Fitting method

The source semantic state contains torso-relative shoulder orientation,
elbow flexion, blended forearm roll, wrist orientation, and normalized hand
position. Target transforms are decoded from the PCA latent model. A damped
Gaussâ€“Newton inverse minimizes semantic residuals, native-pose distance, and
native shoulder/elbow/overlap/wrist joint gaps. A sequence solve adds nearest
native, velocity, and acceleration regularization. Grouped RBF regression is
kept as a baseline; its five-fold training-action CV selected width factor 0.5
and ridge alpha 0.1, but its output is not used as the final proposal.

## Result and stop condition

The preview action bakes and replays, but the fitting result fails validation
and must not be treated as a successful retarget:

- Maximum requested-pose to evaluated-pose matrix error: **1.856047**, first
  divergence at frame **54**, `bone_32` (finger/hand context).
- Baked-action replay matrix error: **0.0**. This only proves the bake
  reproduces the evaluated, divergent pose.
- Joint gaps remain at their native values (maximum range excursion below
  `4e-7` armature units); finite-value check passes; maximum scale delta from
  the validated walk is `8.35e-7`.
- Semantic RMSE: shoulder rotation `(0.840, 0.927, 0.765)` rad; elbow
  flexion `1.191` rad; forearm roll `1.827` rad; wrist rotation
  `(0.678, 0.421, 0.165)` rad; normalized hand displacement
  `(0.615, 0.349, 0.773)`.
- The semantic fit gate and pose-write gate both fail. The preview is saved
  for debugging, not accepted for user visual sign-off. Do not start right-arm,
  full-body, or moveset fitting from this result.

The first mismatch is in a finger context bone even though shoulder/elbow/wrist
joint landmarks remain connected. Debugging should compare the decoded
`bone_31` parent and `bone_32` child matrices before and after local-basis
application, then verify that the semantic inverse remains inside the native
latent manifold. The preview code now snapshots the validated walk action and
clears it on the duplicate before writing matrices, so the source action
cannot overwrite fitted channels.

## Preview contents

Local preview (ignored by Git):
`tools/blender/reports/luigi_walk_semantic_waluigi_left_arm.blend`

- Collection: `SemanticWalk_Waluigi_LeftArm_Preview`
- Rig: `SemanticWalk_Waluigi_Target_Rig`
- Action: `Waluigi_LuigiWalk_SemanticLeftArm_v1`
- Meshes: `SemanticWalk_Waluigi_29_0x75c1640_submesh0` and
  `SemanticWalk_Waluigi_29_0x75c1640_submesh1`

The preview is for diagnostics only. If opening it to inspect the mismatch,
check frames 0, 15, 30, 45, and 60, with special attention to the fingers at
frame 54. Do not accept it as visually validated while the pose-write and
semantic gates fail.

## Preview visibility repair

The first saved preview had the expected scene links, visible preview
collection, and correct armature modifiers, but the duplicated meshes and rig
inherited `Object.hide_viewport = true` from their source objects. The builder
called `hide_set(False)`, which clears the per-view-layer hide state but does
not clear the separate object-level `hide_viewport` flag. The saved mesh and
rig therefore remained hidden. Their object transforms and modifier targets
were otherwise correct.

The generator now explicitly clears both object-level viewport/render hiding
and per-layer hiding, checks that the preview collection is linked and
included in the active view layer, and verifies each preview mesh is bound to
the disposable rig before saving. The existing preview file was repaired and
saved with those visibility settings. Reopening it in Blender confirmed both
mesh objects and the rig are visible in the active scene/view layer, at frame
0, with the intended armature modifiers.

## Corrected baseline semantic fit (v2)

The v1 semantic residuals were dominated by a mismatched full-body baseline. The v2 builder starts from `Waluigi_Arm_Clean_Backup`, assigns a duplicate-owned copy of `Waluigi_GalaxyWalk_Legs_Baked` to retain the validated legs/torso walk, and binds the complete `ARMTEST_` preview meshes to that duplicate. Its 43.437 object scale is preserved. The fitted action is new and baked; the production rig and production actions are not modified. Only the 38-bone context model supplies semantic pose changes; nonmodeled pose channels, root, cap, and legs remain at the validated walk/clean context.

The objective retains native-pose regularization and temporal smoothness. For this run: inverse native prior weight 0.002 with 32 iterations; sequence native weight 0.005, velocity weight 0.01, acceleration weight 0.1. The gate requires at least 25% improvement in each semantic feature group over v1.

| Feature group | v1 RMSE | v2 RMSE | Improvement |
|---|---:|---:|---:|
| Shoulder orientation | 0.84648 rad | 0.53069 rad | 37.3% |
| Elbow flexion | 1.19106 rad | 0.65608 rad | 44.9% |
| Forearm roll | 1.82702 rad | 1.01731 rad | 44.3% |
| Wrist orientation | 0.47061 rad | 0.18354 rad | 61.0% |
| Hand position | 0.60496 | 0.43442 | 28.2% |

The weakest improvement is hand position, though it passes the gate. The largest individual semantic component residual is shoulder Z rotation at frame 51 (1.18087 rad, bone_23 semantic shoulder component). This remains a prototype with meaningful residual error; visual review is required before expanding scope.

Baseline preservation checks passed: maximum nonmodeled pose matrix error 7.15e-7, foot-tail error 1.64e-5, head/cap centroid error 3.15e-5, preserved scale delta 8.35e-7, and baked replay matrix error 8.05e-7. Runtime constraints: zero. Fresh-process saved-preview validation also passed.

Preview: `tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_v2.blend` (ignored local artifact). The reproducible fit and saved-file validation summaries are `tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_v2_fit.json` and `tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_v2_saved_validation.json`. Inspect frames 0, 15, 30, 45, 51, and 60; frame 51 is the worst residual.

## Elbow semantic correction (left arm)

Source elbow extraction is a geometric bend measure from Luigi's `ArmL1.tail` through `ArmL2.head` to `ArmL2.tail`, computed from evaluated action transforms over frames 0–60 and expressed relative to the source rest angle. The raw curve ranges from -0.5863 to -0.0833 rad (frame 6 minimum, frame 36 maximum). The v2 fitter compared that absolute value directly with the Waluigi model's rest-relative training range, -0.0001 to 2.5066 rad. This gave the two rigs different semantic zeros and put the whole Luigi curve below the target training range.

The elbow feature is now rebased to Luigi frame 0 and given a 1.5 excursion gain; the standardized elbow residual has weight 16 while the other semantic residual weights remain unchanged. The fitted preview keeps the existing clean walk baseline and action baking pipeline. The target model still imposes the native joint-gap terms for both overlapping elbow branches. A fresh-process saved-preview replay passes with max semantic report delta 5.64e-8, zero constraints, and the existing 43.437 scale preserved.

| Elbow measure | v2 (recomputed with same 1.5 gain) | Elbow v1 |
|---|---:|---:|
| Mapped-source semantic RMSE | 0.2385 rad | 0.2333 rad |
| Main branch geometric angle range | 0.1735–0.4796 rad | 0.2166–0.5911 rad |
| Overlap branch geometric angle range | 0.1736–0.4797 rad | 0.2166–0.5912 rad |
| Source/target elbow curve correlation | 0.423 | 0.553 |

The target's maximum main-branch bend is at frame 31; the overlap branch maximum is at frame 23, differing by less than 0.0002 rad in branch angle. The branch geometry uses bone_23 as the shoulder, bone_26 through the bone_24 main-chain context, bone_29 through the bone_25 overlap context, and bone_27 for the wrist endpoint. This confirms the two deformation branches bend together in the evaluated preview. The measured angle excursion grows from about 17.5° in v2 to 21.5° here.

Increasing only the elbow residual weight from 4 to 16 did not change the solved elbow curve, so the earlier underweight was not the only limitation: the PCA latent fit has limited elbow leverage around the current solutions. The 1.5 gain changed the branch excursion but only slightly reduced RMSE versus v2 evaluated with the same mapping. Therefore this is a focused improvement, not proof that the visually perceived elbow issue is fully resolved. Inspect the complete preview at frames 6 (least bend), 23 and 31 (maximum branch bend), 36 (source maximum), and 51. Do not extend to the right arm until the elbow is visually confirmed.

Fit report: `tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_v5_elbow_fit.json`.
Saved-file validation and full elbow curves: `tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_v5_elbow_validation.json`.
Local preview (ignored by Git): `tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_v5_elbow.blend`, rig `SemanticWalk_Waluigi_Target_Rig`, action `Waluigi_LuigiWalk_SemanticLeftArm_Elbow_v1`.
