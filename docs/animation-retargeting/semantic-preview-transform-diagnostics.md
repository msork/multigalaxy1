# Semantic preview transform diagnostics

## Root cause

The failed semantic preview did not use the same mesh-to-armature transform
relationship as the visually validated PCA preview. Its rig was under a
43.437-scale root, while each bound mesh had local scale `0.02302185` and world
scale `1`. In the PCA preview, the rig and bound meshes both had world scale
`43.437` and the mesh local scale was `1`. The semantic preview therefore
presented the Armature modifier with a mesh-to-rig scale ratio of `1/43.437`
instead of `1`; this caused the large polygon deformation. The production rig
and source assets were not edited.

The no-op builder now follows the validated PCA layout: the duplicate rig owns
the intentional 43.437 object transform, and bound preview meshes have unit
local scale and the same world transform. Each duplicate receives a separate
action with the validated walk slot's channels copied into a slot owned by
that duplicate. The semantic fitting math was not changed.

## Saved-file no-op gate

`tools/blender/test_semantic_preview_noop.py` creates a complete two-mesh
Waluigi preview with the copied walk channels and no semantic pose edits.
`tools/blender/validate_semantic_noop_saved.py` reopens the saved `.blend` in a
fresh Blender process and compares every evaluated pose matrix, local basis
matrix, and world-space mesh vertex over frames 0–60.

Latest validation: 184,220 vertex samples across 61 frames; maximum world-space
vertex error `0.0`, RMSE `0.0`, maximum pose-matrix error `0.0`, maximum
matrix-basis error `0.0`. The preview moves (`94.89495` maximum vertex
displacement from frame 0), confirming that the comparison is not between two
static meshes. The saved validation record is
`tools/blender/reports/semantic_preview_noop_saved_validation.json`.

The disposable file to open is
`tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_noop.blend`.
Its visible collection contains `SemanticNoOp_Preview_Rig` and both
`SemanticNoOp_Preview_29_0x75c1640_submesh*` meshes. The reference duplicate is
hidden. The previous semantic-fit preview remains a failed experiment and has
not been regenerated or used as a basis for further fitting.
