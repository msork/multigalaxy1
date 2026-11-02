# Bilateral native Waluigi arm calibration

## Dataset construction

`extract_raw_sluggies_calibration.py` now emits schema v2 with 67 profile bones:
eight shared torso/root bones, both eight-bone arm structures, and left/right
hand, finger, and helper context through bone 86. It imports all nine raw ANM
banks (6–14) against the matching `.sluggie`. Every keyed/subframe sample has
both sides' pose transforms, branch landmarks, weighted mesh diagnostics, and
independently computed rest-calibrated semantic features. Right bones are
sampled from their own ANM TrackIds; the extractor asserts the arm and hand
TrackId maps before processing actions. It does not mirror left samples.

The anatomy frame has a shared character-right X axis. `hand_delta_x` is a
signed global anatomical lateral coordinate: positive is toward character
right, so outward displacement is negative for the left arm and positive for
the right. No per-side X sign flip is applied. Shoulder/wrist rotation vectors
and forearm roll are computed in each side's own rest-calibrated frames.

The gzip dataset stays local and Git-ignored because it is large and derived
from proprietary source data. Rebuild it in Blender 5.2.3 with Sluggies IO:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' -b `
  --python tools/blender/extract_raw_sluggies_calibration.py -- `
  --output tools/blender/reports/native_waluigi_raw_anm_bilateral_v2.json.gz `
  --validation-output tools/blender/reports/native_waluigi_raw_anm_bilateral_v2_validation.json `
  --no-preview --no-compare-dataset
```

Run the read-only coverage audit on the Luigi walk workfile:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' -b `
  tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend `
  --python tools/blender/audit_bilateral_semantic_coverage.py -- `
  --dataset tools/blender/reports/native_waluigi_raw_anm_bilateral_v2.json.gz `
  --previous-dataset tools/blender/reports/native_waluigi_raw_anm_calibration.json.gz `
  --output tools/blender/reports/luigi_walk_bilateral_semantic_coverage_v1.json
```

## Extraction validation

The extraction contains 145 actions from all nine banks. Every action has
left and right arm tracks. Each side has 5,284 exact samples total: 4,116
training and 1,168 action-level holdout samples. Identity replay over all
5,284 samples has maximum pose-matrix error `7.63e-6` and maximum full-mesh
vertex error `1.07e-4` world units. The production workfile and its rigs/actions
were not modified.

Right arm mapping:

| TrackId | Bone | TrackId | Bone |
| ---: | --- | ---: | --- |
| 22 | `bone_57` | 117 | `bone_58` |
| 118 | `bone_59` | 23 | `bone_60` |
| 113 | `bone_61` | 114 | `bone_62` |
| 24 | `bone_64` | 25 | `bone_65` |

Right hand/finger mappings are asserted from the `.sluggie` metadata:
`26→66`, `94→67`, `95→68`, `38→69`, `96→70`, `97→71`, `98→72`, `99→74`,
`100→75`, `101→76`, `102→78`, `103→79`, `104→80`, `105→82`, `106→83`,
`107→84`, and `3→86` (bone names use the `bone_` prefix).

## Semantic coverage result

The audit compares Luigi left/right walk features separately against the
matching Waluigi side. “Before” uses the previous left-only Waluigi data as the
right-side proxy. The coverage criterion flags a source frame unsupported in
a feature when the nearest native value differs by more than 0.15 rad for
rotation/roll, 0.08 rad for elbow bend, or 0.08 normalized units for hand
position.

| Side/feature | Unsupported walk frames before | After |
| --- | ---: | ---: |
| Left `hand_delta_x` | 35/61 | 35/61 |
| Right `hand_delta_x` | 34/61 | 23/61 |
| All other individual features, both sides | 0/61 | 0/61 |

For right hand lateral motion, nearest-value p95 error improved from `0.533` to
`0.362`; source-range overlap increased from `35.3%` to `55.6%`. Left coverage
is unchanged, with `0.528` p95 error and `36.0%` range overlap. Joint nearest
pose distance after extraction has median/p95 `1.698/1.988` standardized
units on the left and `1.461/2.148` on the right.

The bilateral data fixes the previous unsupported-right-shoulder problem, but
signed hand lateral position remains outside tolerance for 35 left and 23
right frames. This does not meet the 90% per-dimension support gate (at most 6
unsupported frames), so the data is **not yet sufficient for another local
native solve**. Do not start fitting until lateral hand coverage is improved
or a separately validated lateral target constraint can remain inside the
measured native support.

The compact result is
`tools/blender/reports/luigi_walk_bilateral_semantic_coverage_v1.json`;
extraction identity diagnostics are in
`tools/blender/reports/native_waluigi_raw_anm_bilateral_v2_validation.json`.
