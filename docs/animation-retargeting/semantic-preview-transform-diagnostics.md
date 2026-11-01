# Semantic preview transform diagnostics

## Earlier transform issue and baseline correction

The earlier no-op comparison had two flawed inputs: it duplicated the
production rig, whose saved frame-zero pose contained unexplained channels,
and it excluded `ARMTEST_...` meshes, comparing two rebuilt copies of the
production submeshes instead. The zero vertex error only showed that those
two copies matched each other. It did not validate the known-good walk, hat
placement, or foot motion.

The semantic preview also had a mesh-to-armature transform mismatch: its rig
world scale was `43.437`, but its meshes had world scale `1` (local scale
`0.02302185`). The validated arm-test meshes use world scale `43.437` and
local scale `1`. The corrected no-op builder uses `Waluigi_Arm_Test` as the
walk source, `Waluigi_Arm_Clean_Backup` as the clean rest/pose basis, and the
full `ARMTEST_...` mesh objects with their original transforms. It copies only
the validated walk's pose channels (legs and torso) into target-owned action
slots, filters stale IK pole-angle channels, and removes constraints from the
preview duplicates. Other bones remain at the clean basis. No production rig,
action, or mesh is edited.

## Saved-file no-op gate

`tools/blender/test_semantic_preview_noop.py` creates a complete two-mesh
Waluigi preview from the validated walk and clean pose basis, with no semantic
pose edits.
`tools/blender/validate_semantic_noop_saved.py` reopens the saved `.blend` in a
fresh Blender process and compares every evaluated pose matrix, local basis
matrix, and world-space mesh vertex over frames 0–60.

The fresh-process baseline comparison covered 184,220 vertex samples across 61
frames. The rebuilt action matched `Waluigi_Arm_Test` with maximum world-space
vertex error `4.9624e-5`, maximum pose-matrix error `0`, foot-tail trajectory
error `6.8771e-6`, and `bone_53` head/cap weighted-centroid error `1.6213e-5`.
After saving and reopening, preview and baseline duplicates matched with zero
maximum pose-matrix, basis-matrix, vertex, foot-trajectory, and head/cap
centroid error. The preview moves (`94.89495` maximum vertex displacement
from frame 0). The saved validation record is
`tools/blender/reports/semantic_preview_noop_saved_validation.json`.

The disposable file to open is
`tools/blender/reports/luigi_walk_semantic_waluigi_left_arm_noop.blend`.
Its visible collection contains `ValidatedWalk_NoOp_Preview_Rig` and both
`ValidatedWalk_NoOp_Preview_ARMTEST_...` meshes. The reference duplicate is
hidden. The semantic-fit preview remains a failed experiment and has not been
regenerated or used as a basis for further fitting.
