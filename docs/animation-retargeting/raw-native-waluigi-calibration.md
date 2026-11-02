# Raw Native Waluigi Animation Calibration

## Status and source boundary

This milestone replaces orphaned `sequence_*` actions as the authoritative
native source with the original Waluigi `.sluggie` model and raw `.anm` banks.
The source files stay under `C:\MultiGalaxy1-Assets\Working\MSS\Waluigi\`;
they are never copied into Git or modified. Blender imports them into a fresh,
disposable background scene through the locally installed Sluggies IO add-on.
The production workfiles and the successful Luigi walk preview remain
read-only.

## Raw-native context model rebuild (2026-11-01)

The production-basis context model now trains only from
`native_waluigi_raw_anm_calibration.json.gz`. The inferred `.004` bundle is
rejected by the shared dataset loader and remains available only for explicit
comparison diagnostics. All nine banks (6–14) contribute their appropriate
actions; the extractor's action-level split is 118 training actions / 27
holdouts, 4,116 / 1,168 exact-time samples. The split is at the action level,
so no samples from a holdout action enter fitting or cross-validation.

The context profile has 38 bones and 228 pose features (six values per bone:
translation plus rotation vector). Five-fold action-grouped training-only CV
selected 224 latent dimensions by the one-standard-error rule. This is a
near-identity representation rather than a compact compression: the 228-D
feature space is poorly reduced by this linear model. On the same raw data and
holdouts, 192 dimensions give aggregate standardized feature RMSE
`1.7263e-8`; 224 dimensions give `1.9625e-15`. These figures should not be
compared directly with the former inferred-data model's 192-D result because
its training source and split differ.

Identity replay across the full profile agrees with fresh native evaluation:
maximum native-vs-dataset context matrix error `2.57e-6`; maximum PCA context
matrix error `5.72e-6`; maximum whole-character overlay vertex error
`4.02e-4` world units (gate `0.01`). All 27 holdout previews passed. The raw
source remains the only training authority; `.004` actions are diagnostic
comparisons only. The compact run result is in
[`raw-anm-context-model-results.json`](raw-anm-context-model-results.json).

The Luigi walk solver was rerun against the 224-D raw model and generated a
disposable complete preview, but the semantic refit **failed** its fresh-file
consistency gate (maximum fit-report vs saved-evaluation semantic RMSE delta
`0.467`, right-side delta `0.461`). The in-memory fit report therefore is not
an authoritative residual report, and the saved preview is diagnostic only.
No claim of semantic improvement or visual acceptance is made. The independent
raw-model identity/PCA validation remains passed. Do not begin other Luigi
animations until the saved-action mismatch is resolved and the walk receives
visual sign-off.

## Verified importer semantics

Use the Sluggies IO add-on's own `.sluggie` and `.anm` import operators. The
`.sluggie` import must use **Reorient Bones = true**, matching the production
Waluigi rest basis. `.anm` tracks bind by their numeric `TrackId` to the
`track_id` custom property on each imported bone. The importer decodes game
local SRT, applies its per-bone correction when `SluggiesBonesReoriented` is
set, and creates one action per sequence with bank, source path, sequence
index/count, and original track-record metadata. Do not port or guess those
conversions in a separate importer.

The extractor requires the installed Sluggies IO Blender add-on and explicitly
fails if its operators or metadata are unavailable. It records the add-on
version, model/bank paths and SHA-256 hashes, TrackId map, source metadata,
rest signature, object matrix/scale, exact key/subframe times, pose/local/rest-
relative SE(3), branch landmarks/relative rotations, static weighted vertex
membership, evaluated weighted centroids, and arm-region mesh positions. It
uses Blender 5's layered action channelbags and assigns every action only to
the newly imported disposable rig.

The profile is character-agnostic at the extractor boundary: model, banks,
profile bones, arm-region bones, meshes, and action split policy are arguments.
The original v1 Waluigi profile stored 38 left-arm context bones. The bilateral
v2 profile stores 67 bones: shared torso/root once, both eight-bone arm
branches, and both hand/finger/helper branches through bone 86. A Wario run
must provide Wario's own `.sluggie`, ANM banks, TrackIds, and profile.

## Provenance evidence

The raw Waluigi package contains the `.sluggie` and `.glb` model, texture PNGs,
and nine native banks 6 through 14 with converted GLBs. Raw ANM import produced
145 actions across those nine banks. The `.sluggie` rig has 92 bones and its
TrackIds match the previously investigated arm mapping exactly:

| Bone | TrackId | Bone | TrackId |
| --- | ---: | --- | ---: |
| `bone_22` | 16 | `bone_25` | 17 |
| `bone_29` | 18 | `bone_30` | 19 |
| `bone_26` | 111 | `bone_27` | 112 |
| `bone_23` | 115 | `bone_24` | 116 |

The left-arm TrackId map is asserted by the extractor and covered by a unit test.
The native model metadata also establishes the right-arm map:

| Bone | TrackId | Bone | TrackId |
| --- | ---: | --- | ---: |
| `bone_57` | 22 | `bone_60` | 23 |
| `bone_64` | 24 | `bone_65` | 25 |
| `bone_61` | 113 | `bone_62` | 114 |
| `bone_58` | 117 | `bone_59` | 118 |

Right hand/finger tracks resolve directly from the imported model metadata:
`26→bone_66`, `94→bone_67`, `95→bone_68`, `38→bone_69`, `96→bone_70`,
`97→bone_71`, `98→bone_72`, `99→bone_74`, `100→bone_75`, `101→bone_76`,
`102→bone_78`, `103→bone_79`, `104→bone_80`, `105→bone_82`, `106→bone_83`,
`107→bone_84`, and `3→bone_86`. The extractor asserts these TrackIds before
sampling. Right-side transforms are evaluated from their own raw ANM tracks;
they are never copied from or reflected out of left-side samples.
Some ANM sequences also contain TrackIds absent from the 92-bone model (such
as `16385`, `16641`, `16643`, `33032`, and `33288`). They are retained in raw
track metadata but are not guessed to be bone transforms or inserted into the
pose profile.

Imported with reorientation enabled, the raw `.sluggie` rest matrices match all
92 production Waluigi rest matrices exactly. Its arm TrackIds resolve to the
same bone names as the established mapping (16/17/18/19 and 111/112/115/116).
The add-on imports the source model
at unit object scale, while the production Waluigi object uses a `+90 degree X`
orientation and intentional uniform scale `43.437`. The extractor assigns the
validated production object matrix to the imported duplicate (without applying
scale) before it evaluates world-space meshes. No per-bone candidate-to-
production correction is needed for raw-bank calibration poses.

Bank 7 (`Waluigi_7_Batting.anm`) has 28 sequences. They map by sequence index
to the 28 former `sequence_*` actions in the `.004` family: all 28 durations
are related by a constant factor of six, and the three anchor pairs are
`sequence_10.003` ↔ `Waluigi_7_Batting_seq10`, `sequence_15.003` ↔
`Waluigi_7_Batting_seq15`, and `sequence_24` ↔ `Waluigi_7_Batting_seq24`.
Pose comparison is performed after converting the candidate-owner matrices
with the already validated per-bone rest formula, at exact keys from both
sources. Similar names/durations alone do not promote an action: the compact
report records per-action transform and mesh residuals and flags outliers.

## Dataset, split, and validation

The raw dataset is local, gzip-compressed, and Git-ignored. Every ANM sequence
is assigned wholly to train or holdout. For the 28 Bank 7 batting sequences,
retain the existing action-level holdouts `sequence_11`, `sequence_15`,
`sequence_20`, `sequence_24`, and `sequence_26`, preserving the existing 23/5
comparison. Other banks use a deterministic per-bank sequence-index split.
No frame-level random split is allowed.

Validation proceeds in this order:

1. Confirm the importer action metadata, sequence counts, TrackId coverage,
   frame ranges, and exact raw key times.
2. Compare the Bank 7 sequence-index actions against converted candidate-owner
   actions in the shared production rest basis. Report full-skeleton, 38-bone,
   arm-branch, and weighted-mesh errors separately; do not hide finger/context
   discrepancies inside an arm-only score.
3. Re-evaluate a no-compression identity replay from extracted transforms on a
   duplicate of the imported rig and compare pose matrices, mesh vertices,
   weighted centroids, and landmarks against the raw imported action.
4. Save a disposable Blender preview with source `.sluggie` meshes and textures
   resolved from the sibling `tex` folder. Inspect representative actions
   visually before replacing the old inferred calibration for fitting.

## Extracted dataset results

The first full extraction imported all nine raw banks and produced 145 actions
and 5,284 exact-key/subframe samples. The complete-action split contains 118
training actions and 27 holdouts; Bank 7 retains the established 23/5 split.
For identity replay, all 5,284 samples reproduced all evaluated pose matrices
to a maximum element error of `7.63e-6`; the maximum world-space mesh vertex
error was `1.08e-4`. This validates importer-to-dataset extraction and replay
for the recorded profile/context, not a new retarget solver.

The raw Bank 7 sequence indices map one-to-one to all 28 legacy `.004` action
indices, and raw durations are six times the legacy durations. Pose application
is not equivalent: across the matched set the maximum context pose-matrix error
is `0.4313`, maximum arm pose-matrix error is `0.2409`, and worst arm-region
vertex error is `13.654` world units (`sequence_11.003` vs raw sequence 11).
The three previously validated anchors also differ: arm-matrix maxima are
`0.0227`, `0.0271`, and `0.0219` for sequences 10, 15, and 24. These results
support the raw `.anm` importer as authoritative native input and disqualify
the converted `.004` pipeline as an exact replacement for it.

The saved preview is generated from the imported source model and packs all
three valid PNGs from its sibling `tex` directory: `1024x128`, `256x128`, and
`512x512`. The two imported meshes are visible, bound to the imported 92-bone
rig, and the intentional scale remains `43.437`. `Waluigi_7_Batting_seq10` at
frame 41 was rendered from the saved preview and visually retains the complete
character and native batting pose. The production rig/actions and existing
Luigi walk outputs were not changed.

Run the complete extractor from the repository root with Blender 5.2.3 and
Sluggies IO enabled:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --disable-autoexec --python tools/blender/extract_raw_sluggies_calibration.py
```

The raw dataset and `.blend` preview are local generated outputs and are
Git-ignored. The compact validation report is committed. This milestone does
not alter the semantic solver or regenerate Luigi→Waluigi walk actions. The raw
dataset can replace the inferred pipeline as the **native Waluigi calibration
source**; semantic fitting remains gated on broader visual review of native
actions and cannot inherit old `.004` action poses as ground truth.
