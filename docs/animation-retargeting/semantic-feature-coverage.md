# Luigi and Waluigi Semantic Feature Coverage Audit

## Purpose and reproducibility

This is a read-only audit. It compares Luigi walk frames 0–60 with all 4,116
raw-ANM Waluigi training samples, excludes the 27 action holdouts, and does not
fit or preview an animation. The Blender script reads the source action curves
and the production-basis calibration JSON; it does not assign or key actions.

Run it from the worktree with Blender 5.2.3:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' -b `
  'tools/blender/reports/luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend' `
  --python tools/blender/analyze_semantic_feature_coverage.py -- `
  --dataset 'tools/blender/reports/native_waluigi_raw_anm_calibration.json.gz' `
  --output 'tools/blender/reports/luigi_walk_raw_anm_semantic_coverage_audit.json'
```

The JSON report contains per-component min/max, quantiles, interval overlap,
nearest native-value errors, 11-dimensional nearest-state distances, side
symmetry checks, independent anatomical-motion correlations, and rankings by
action and bank.

## Coordinate and rest-offset findings

The previous extractor expressed source channels in Waluigi’s torso/forearm
rest axes. This did convert the rigs into a common Waluigi rest basis, but the
axes remained dependent on imported bone roll and rest orientation. Forearm
roll also assumed local +Y for both overlapping branches. The new coverage
pass derives +X from the labeled left-to-right shoulder line, +Y from torso-up,
and +Z from their right-handed cross product. It derives each forearm twist
axis from that side’s elbow-to-wrist rest vector and projects body-forward for
the remaining forearm frame. Object scale is removed before constructing axes.
The test suite checks that these frames remain stable under uniform scale and
object rotation.

Elbow bend had a larger reference mismatch. Luigi’s left rest bend is 0.7613
radians; Waluigi’s is 0.0103 radians. The old graph then subtracted Luigi’s
frame-0 value (-0.5311 radians on the left, +0.1316 on the right) and scaled
the remaining excursion by 1.5, while Waluigi samples remained referenced to
Waluigi rest. The calibrated feature is the absolute geometric bend angle from
a straight limb, evaluated identically for both rigs. Luigi walk bend spans
0.1750–0.6780 radians; Waluigi training spans 0.0007–2.5169 radians.

The anatomical-frame change reduced the median 11D nearest-native distance
from 2.099 to 1.698 standardized units (about 19%). This indicates the old
component frames contributed to joint mismatch. It did not remove all joint
mismatch: the remaining median distance is about 0.51 standardized units per
feature RMS.

## Range coverage

| Feature group | Before: Luigi range | Before: Waluigi range | Before overlap | After: Luigi range | After: Waluigi range | After overlap |
|---|---:|---:|---:|---:|---:|---:|
| Shoulder orientation magnitude | 0.766–1.002 | 0.090–2.708 | 100% | 0.766–1.002 | 0.090–2.708 | 100% |
| Elbow bend | -0.083–0.672, graph-adjusted rest delta | -0.010–2.507, Waluigi rest delta | 90.3% | 0.175–0.678, angle from straight | 0.001–2.517, angle from straight | 100% |
| Forearm roll magnitude | 0.731–0.892 | 0.002–3.132 | 100% | 0.008–0.492 | 0.001–3.134 | 100% |
| Wrist orientation magnitude | 0.025–0.430 | 0.003–2.013 | 100% | 0.025–0.430 | 0.003–2.013 | 100% |
| Normalized hand-position magnitude | 0.502–0.676 | 0.124–2.313 | 100% | 0.502–0.676 | 0.124–2.313 | 100% |

All five group magnitudes have native values close to every Luigi frame. For
the calibrated elbow feature, nearest native absolute error has median 0.00076
radians and 95th percentile 0.00377 radians. The calibrated forearm-roll
nearest error has median 0.00015 radians and 95th percentile 0.00069 radians.
The per-axis report is more informative than magnitude overlap: calibrated
hand lateral displacement has weaker signed coverage than the other groups,
and should retain its directional residual in any later solve.

## Why the previous graph retained little motion

Feature ranges do not explain the six-percent elbow-retention result. The old
graph-adjusted elbow interval overlapped 90.3% of its source range, and its
per-frame nearest raw-ANM elbow value had median error 0.0009 radians. With a
shared straight-arm zero, all Luigi elbow angles are covered. The failed path
instead selected `Waluigi_6_Fielding_seq21` sample 9 for 57 of 60 transitions.
That is a dynamic-program path-selection collapse: the joint state and edge
costs preferred a static pose despite available per-feature values. This audit
does not change graph weights or the fitter.

## Actions, symmetry, and semantic correlations

By joint semantic coverage, `Waluigi_6_Fielding_seq21` ranks first (mean
standardized nearest distance 1.851; 26 samples). `Waluigi_8_Running_seq10`
ranks a close second (1.879; 61 samples; 90.9% mean feature-range overlap) and
is the strongest walk-shaped candidate because it comes from the Running bank
and spans the same 61 source frame samples. The Fielding bank has the best
aggregate bank score by a small margin; the Running bank is nearly tied and
contains the temporally matched locomotion clip. Exact rankings for all
actions/banks are in the JSON report.

The calibrated Luigi left/right features are not identical under expected
mirror signs: elbow trajectories correlate closely but differ by about 0.65
radians RMS; shoulder and wrist component correlations also differ by axis.
The 38-bone raw calibration profile contains Waluigi’s left arm only, so its
right-arm symmetry is still inferred by the existing homologous mirror map.
The report labels this limitation rather than treating mirrored source and
target motion as measured symmetric data.

The semantic channels track their intended geometry: shoulder orientation
magnitude correlates nearly perfectly with relative shoulder rotation; elbow
bend correlates 0.94/0.98 with independently measured Luigi left/right elbow
angles and essentially 1.0 for raw Waluigi; wrist orientation correlates
nearly 1.0 with forearm-relative wrist rotation. Hand-position correlations
are 0.91/0.94 for Luigi and 0.63 for Waluigi. Forearm roll uses signed twist
around each rest-local distal anatomical axis.

## Decision

There is enough per-feature semantic coverage to begin a new constrained graph
solve using the calibrated features. The six-percent elbow result was not
caused by absent native elbow values. Start candidate exploration with the
61-sample Running action and preserve the elbow angle-from-straight reference.
The coverage result does not establish that a continuous, anatomically
coherent full-pose path exists; the next solve must keep the existing jump,
glove, and temporal-path gates.
