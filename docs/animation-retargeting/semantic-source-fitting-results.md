# Luigi Walk to Waluigi Left Arm: Prototype Run

## Input and output

- Source owner: `luigi.001_armature`, with action
  `luigi.001_armature_walk`, slot `OBluigi.001_armature`, frames 0–60.
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
Gauss–Newton inverse minimizes semantic residuals, native-pose distance, and
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
