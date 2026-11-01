# Native Arm Context-Bone Expansion

## Goal and source boundary

The current model reconstructs the eight requested arm transforms but does not
reproduce production-mesh centroids because the surrounding converted native
pose was neutralized. This milestone identifies the smallest sufficient set of
co-influencing and animated bones, expands the native dataset, and verifies
identity reconstruction before any compressed model is fitted.

The input remains the existing local candidate-owner action scene and the
already validated candidate-to-production conversion. Analysis must evaluate
the 28 actions in the `OBArmature.004` family on disposable armature/data
duplicates and convert all 92 pose matrices into production basis. The source
scene, production rig, source actions, and source mesh data are read-only. Keep
the intentional 43.437 object scale unchanged.

## Context discovery

1. For every production mesh vertex with nonzero weight in one of the eight
   arm groups, inventory all nonzero vertex-group influences on that same
   vertex. Aggregate vertex count, summed weight, and fraction of affected arm
   vertices per group/bone.
2. Add the ancestors of every co-influencing bone to a hierarchy-dependency
   candidate set, recording which mesh regions depend on each ancestor.
3. Evaluate candidate-owner actions at the union of exact FCurve key times for
   all skeleton channels plus action endpoints. Convert the full evaluated pose
   per sample. Measure each candidate's rest-relative translation/rotation
   motion and its weighted skinning displacement on the vertices selected in
   step 1.
4. Rank by (a) co-influence coverage, (b) accumulated and maximum weighted
   deformation displacement, and (c) hierarchy dependency. A bone that is
   animated but has no skinning or hierarchy effect on the arm region is not
   added just because it has motion.

The rank is evidence for choosing a compact context profile, not an automatic
claim that all lower-ranked bones are irrelevant. The first expanded profile
includes all direct co-influences, their animated ancestor chain, and complete
finger branches under the glove. Identity replay is the gate before PCA.

## Expanded dataset and identity gate

The dataset builder accepts explicit context bones and explicit arm-region
groups, so a Wario profile can supply its own groups. It preserves the
whole-action 23 train / 5 holdout split and samples the union of exact keyed and
subframe times for the selected context bones plus action endpoints. Every
sample stores converted production-basis rest-relative transforms, weighted
centroids both over complete groups and over the arm-region vertex mask, exact
world positions for vertices in that mask, and semantic landmarks. The raw
source channels are never emitted as model features.

Before compression, identity reconstruction assigns the extracted transforms
back to a disposable production-basis rig, with unselected bones neutral, then
compares evaluated mesh vertices and landmarks against full converted native
captures saved in the dataset. The identity model performs no dimensionality
reduction or learned interpolation. Failure means expand the context profile;
do not fit PCA. Only after identity reconstruction passes on train and all five
holdouts may PCA be fitted. RBF and semantic retargeting are out of scope for
this milestone.

## Current Waluigi profile results

The original eight-bone PCA preserved the requested transforms but produced
large mesh-centroid errors because native finger, torso, and other co-weighted
context was neutralized. The profile scan found 239 affected vertices. Eight
additional groups directly share those vertices:

| Context bone | Affected vertices | Actions with local motion | Max local rotation | Weighted deformation rank |
| --- | ---: | ---: | ---: | ---: |
| `bone_20` | 40 | 28 | 101.84° | 206.39 |
| `bone_35` | 9 | 20 | 91.13° | 167.54 |
| `bone_43` | 9 | 21 | 103.57° | 153.13 |
| `bone_47` | 10 | 21 | 85.99° | 148.55 |
| `bone_39` | 9 | 21 | 104.69° | 147.66 |
| `bone_31` | 5 | 21 | 81.61° | 122.04 |
| `bone_48` | 1 | 21 | 42.98° | 52.19 |
| `bone_52` | 1 | 21 | 47.33° | 3.83 |

The 38-bone profile adds 30 bones to the original arm set: direct mesh
co-influences above, spine/root ancestors (`bone_1`, `bone_2`, `bone_3`,
`bone_5`, `bone_6`, `bone_19`, `bone_21`), and the full animated finger
branches (`bone_32`–`bone_34`, `bone_36`–`bone_38`, `bone_40`–`bone_42`,
`bone_44`–`bone_46`, `bone_49`–`bone_51`). These extra branches preserve
parent-space orientation and glove/finger shape; zero-weight arm helpers
`bone_24` and `bone_26` remain in the profile because they are animated
kinematic links in the arm hierarchy.

The rebuilt dataset has 1,107 keyed/subframe samples (845 training, 262
holdout) across 28 actions. Its feature width is 38 × 6 = 228 scalar values.
No-compression replay passed all 28 actions: maximum pose-matrix error
`3.82e-6`, arm-region vertex error `1.74e-4` world units, weighted arm-region
centroid error `1.68e-4`, and landmark error `1.42e-4`. This confirms that the
expanded transforms reproduce the mesh region captured from the full converted
native poses.

PCA-only fitting selected 192 latent dimensions from 228 features by
five-fold action-grouped CV on training actions only. The holdouts remain
`sequence_11.003`, `sequence_15.003`, `sequence_20.001`, `sequence_24`, and
`sequence_26`. PCA holdout reconstruction on disposable production-basis rigs
has maximum arm-region vertex error `1.49e-4`, centroid error `1.44e-4`, and
landmark error `1.27e-4` world units. The small retained residual is consistent
with float32 prediction storage and Blender matrix evaluation. These results
validate the identity and linear reconstruction path only; they do not validate
a Luigi semantic driver or a retargeted motion manifold.

## Full-character holdout overlay previews

The arm-region PCA report alone is not a useful visual check: a character
preview must retain the native pose context and every accessory. The preview
builder evaluates each held-out native action on a disposable candidate-owner
duplicate, converts the full pose to production basis, and duplicates the
complete production character (both bound submeshes and their associated child
objects) into separate native and PCA collections. The PCA copy starts from
the full converted native pose; only the 38 profile bone transforms are
replaced by the training-only PCA projection of that held-out pose. Root/object
transforms, the intentional 43.437 scale, hat/cap, face, fingers, and all
non-profile bone transforms remain native. No production object, rig, or
action is written.

Each holdout is saved as an independent local `.blend` under
`tools/blender/reports/native_waluigi_context_pca_holdouts/`. The PCA collection
is visible on open. To compare the native reference, show its
`NativeReference_<action>` collection in the Outliner and hide
`PCAOverlay_<action>`; showing both at once overlaps coincident geometry.
`validate_context_pca_preview_file.py` reopens each saved file and compares
world-space vertices at every action key and midpoint. All five holdouts pass;
the largest saved-file vertex difference is `1.56e-4` world units across both
submeshes (3,020 vertices per sampled time), with root and mesh object matrices
matching exactly. This is an overlay/reconstruction check, not proof that the
PCA projection is semantically suitable for a different source character.

The live converted holdout context can differ from the stored dataset's
selected-bone transforms (worst observed discrepancy: `bone_48` in
`sequence_26`, about `4.57` matrix units at frame 0). The previews deliberately
project the live native held-out profile into the training PCA subspace so the
comparison isolates PCA reconstruction while retaining the live native
context. Resolve this dataset/action-basis discrepancy before using the
dataset for semantic source fitting.

## Reusable profile boundary

Bone names, arm influence groups, mesh objects, holdout IDs, and hierarchy
roots are dataset/profile arguments. The discovery logic operates on any
matching source/target skeleton and skinned mesh, so it can be reused for Wario
after a Wario native source and production-basis profile have been validated.
No Waluigi names are embedded in the generic weight/deformation ranking math;
the current left-arm bone list is an explicit profile input.
