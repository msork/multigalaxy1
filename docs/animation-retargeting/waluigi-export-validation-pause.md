# Waluigi export validation pause

Status: **paused pending a validated Waluigi BDL export**.

Do not resume Luigi-to-Waluigi retargeting until the Sluggers export pipeline has
been validated for geometry, skeleton alignment, and bone-driven deformation.
The newly reported export concern is that Sluggers models may need scale and
rotation normalization, plus mesh-translation baking, before SuperBMD conversion.
This is a hypothesis to test against the source/export pipeline; no conversion or
normalization has been applied to source assets as part of this checkpoint.

## Preserved state

- The approved literal Luigi-to-Waluigi WALK V2 remains the golden reference.
- The literal converter and its character profile are unchanged.
- No V5 animation correction has been implemented.
- No original Mario Super Sluggers assets, production rigs, or production actions
  were modified.
- The hybrid adapter remains diagnostic-only and is not selected as a replacement
  for the literal converter.

## Current rigging progress

The literal converter has been exercised on WALK, RUN, JUMP, and SWIM. WALK V2
has user approval. RUN, JUMP, and SWIM still have the following visually reported
left-arm defects:

- RUN: backward left-arm swing in frames 5–14 and 24–46.
- JUMP: downward left-wrist twist in frames 13–14 and incorrect wrist orientation
  across frames 9–24.
- SWIM: backward left-elbow motion at frame 5 and downward forearm direction in
  frames 11–14.

V3/V4 diagnostics found that pose-angle metrics alone did not establish visible
improvement. Corrections can affect the wrong deform control, have little effect
in portions of a correction window, or move weighted vertices without correcting
the visible joint direction. A read-only V5 root-cause diagnostic script was
started to compare source and target transforms and probe individual deform
controls through evaluated mesh vertices. This investigation is now paused at
the user's export-pipeline validation gate; its findings are not a basis for
implementing corrections.

## Resume gate

Before resuming retargeting, validate a disposable Waluigi export/import round
trip and record:

1. Geometry agreement against the source model, including object/world transforms.
2. Skeleton alignment and rest-pose/bind-matrix agreement.
3. Bone deformation on representative weighted regions, including both arms,
   gloves/fingers, torso, and legs.
4. Whether scale normalization, rotation normalization, or mesh-translation
   baking is actually required, with measured before/after results.

Keep all experiments on disposable copies. Preserve source assets and the golden
WALK V2 file byte-for-byte.

## Files in this checkpoint

Committed in this checkpoint:

- `tools/blender/diagnose_literal_v5_root_cause.py` — read-only Blender diagnostic
  for mapped arm chains, vertex-weight regions, and disposable deform-control
  perturbations.
- This document — pause status, preserved state, unresolved defects, and resume
  gate.

Generated diagnostic JSON and renders under
`tools/blender/reports/literal_v5_root_cause/` remain local and uncommitted. They
are retained for resumption. Other pre-existing untracked reports and preview
files were left untouched.
