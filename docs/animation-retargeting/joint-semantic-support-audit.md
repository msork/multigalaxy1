# Joint semantic support audit

## Scope and method

This is a read-only audit of `luigi.001_armature_walk` frames 0–60 against the **training split only** of `native_waluigi_raw_anm_bilateral_v2.json.gz`. It recomputes the 11 rest-calibrated anatomical features on both Luigi sides and the independently extracted real Waluigi left/right samples. It does not fit a retarget, build a preview, or mutate production data.

The 11D feature order is shoulder rotation xyz, elbow flexion, forearm roll, wrist rotation xyz, and arm-plane hand radial/tangential/depth. Distances are standardized by each side's native training standard deviation (floored at 0.05). The report includes nearest-sample Euclidean distance, shrinkage Mahalanobis distance within neighborhoods, marginal and group support, pairwise convex-hull checks, and nonnegative convex mixtures. Convex mixture weights are shared between semantic vectors and torso-relative rest-relative arm transforms; exact semantics are recomputed after decoding those mixed transforms. A zero bone-coordinate range excess only means a convex average remains inside each selected sample's coordinate bounds; it does not guarantee a valid deformation.

The 96-neighbor continuity set uses the current temporal neighbor retention heuristic. Neighborhood axis-box support is intentionally diagnostic, not a sufficient safety test. Mahalanobis values are descriptive distances, not p-values or chi-square tests.

## Results

There are 4,116 native training samples per side. Nearest joint-sample distances are left mean 1.570, p95 2.097, max 2.108 standardized units; right mean 1.419, p95 1.728, max 1.755.

| Support test | Left walk frames unsupported | Right walk frames unsupported |
| --- | ---: | ---: |
| 96 nearest samples, axis-aligned box | 29 | 21 |
| continuity-biased 96-neighbor set | 39 | 38 |
| 384-neighbor axis box | 0 | 12 |
| 512-neighbor axis box | 0 | 4 |
| all 4,116 samples, axis box | 0 | 0 |

The current continuity neighborhood has high local Mahalanobis p95: 8.14 left and 8.72 right. The full-dataset values fall to 3.24 and 4.61, respectively, but still show joint separation. The axis-box result becomes permissive as neighborhoods grow because independent feature extrema can come from different poses; it does not establish that one supported combination exists.

### Feature combinations

Every left 2D feature projection contains all 61 Luigi points in its global native convex hull. On the right, `elbow_flexion + hand_radial` falls outside its 2D hull for frames 34–44. The fact that almost all pairs pass while the full 11D distance remains high points to higher-order interaction and distribution mismatch, rather than one universally missing scalar feature.

Nearest group distances (p95, standardized) rise as features are combined:

| Group | Left | Right |
| --- | ---: | ---: |
| shoulder | 0.194 | 1.024 |
| elbow | 0.009 | 0.001 |
| forearm roll | 0.003 | 0.003 |
| wrist | 0.138 | 0.285 |
| hand plane | 0.263 | 0.223 |
| shoulder + elbow | 0.782 | 1.260 |
| shoulder + wrist | 0.938 | 1.330 |
| elbow + wrist | 0.702 | 0.406 |
| wrist + hand plane | 0.718 | 0.844 |
| shoulder + elbow + wrist | 1.360 | 1.435 |
| all 11 features | 2.097 | 1.728 |

The left 96-neighbor failure frames cluster at 8–46. Their nearest full-vector samples disagree most often in forearm roll, shoulder x, elbow flexion, wrist x/y, and hand depth. This is the region where the source shoulder and elbow excursion overlap in time, while the target dataset's nearest poses tend to differ in wrist orientation and roll. The worst left nearest match is frame 20 (`Waluigi_10_Catcher_seq3`, normalized time 0.20; distance 2.108): the largest standardized differences are wrist y 0.990, wrist x 0.891, elbow 0.838, shoulder x 0.834, and hand depth 0.788.

On the right, continuity-neighborhood failures are frames 0–1, 15–33, and 44–60. They are dominated by a joint shoulder orientation mismatch (all three components), then forearm roll and hand depth. The worst is frame 53 (`Waluigi_6_Fielding_seq3`, normalized time 0.903; distance 1.755): shoulder x/y/z differences are 0.808/0.591/0.571 standardized, with hand depth 0.648 and wrist z 0.621. Frames 34–44 have the separate elbow-flexion/hand-radial pairwise hull conflict.

### Marginal ranges and left shoulder

All 11 scalar features are individually covered closely: nearest marginal p95 errors are at most 0.004 on the left and 0.009 on the right in feature units. Luigi's shoulder-x excursion range is 0.344 of the native range on the left and 0.305 on the right; shoulder y/z ranges are smaller still. Thus the left shoulder's earlier 39.2% retained excursion is **not explained by a lack of shoulder-only amplitude**. The left shoulder-only nearest 3D group distance is low (p95 0.194), but the nearest shoulder+wrist distance rises to 0.938 and shoulder+elbow+wrist to 1.360. The loss is consistent with competing joint context and deformation coupling, not simply a shoulder range clamp. This audit does not attribute the exact 39.2% solver outcome to one objective term because it does not rerun or instrument that solver.

### Native actions with best coverage

Actions are ranked by how often their sample is the full-11D nearest sample for a continuity-neighborhood failure frame:

- Left: `Waluigi_6_Fielding_seq21` (13 frames), `Waluigi_10_Catcher_seq3` (11), `Waluigi_8_Running_seq10` (10), then `Waluigi_6_Fielding_seq22` (5).
- Right: `Waluigi_6_Fielding_seq3` (29 frames), then `Waluigi_8_Running_seq16` (9).

These rankings identify useful source segments to inspect, but do not imply that concatenating them is temporally valid. The left difficulty region is mostly approached by a few poses from different actions; the right is more consistently nearest to Fielding sequence 3.

### Convex interpolation diagnostic

Projecting each target semantic vector onto the convex hull of the nearest 96 semantic samples converges for all frames. Despite that, the p95 projected 11D residual is 1.206 left and 1.177 right standardized units, and **zero** frames on either side meet all per-feature support tolerances. Convex mixing transforms with those same weights and recomputing semantics also yields zero exact-tolerance frames on both sides.

Using all 4,116 samples reduces the semantic projection residual but does not solve the issue: 11/61 left frames and 0/61 right frames meet all semantic tolerances in feature space. After decoding the mixed transforms and recomputing nonlinear semantics, only 7 left frames and 0 right frames meet all tolerances. The full-right convex projection did not meet the solver's duality-gap convergence criterion, so that particular residual is indicative rather than a certified exact hull distance; the converged 96-neighbor projection already establishes that the current local neighborhoods do not support the target to the required tolerance.

No per-bone transform coordinate exceeds the chosen candidate sample bounds under a valid convex mixture. This is expected from convex weights and is not evidence of anatomical validity. Convex interpolation alone therefore does not provide adequate semantic or deformation support.

## Diagnosis and recommendation

The 96-neighbor test is too strict as a **box membership test** in some frames (39/38 failures versus 29/21 for ordinary nearest samples), because continuity retention changes which samples are available. However, relaxing k is not a safe fix: axis boxes become permissive, while local convex projection remains far outside the semantic tolerances and exact decoded semantics remain mismatched. The root cause is joint feature coupling and a dataset whose native poses distribute motion differently, not missing scalar shoulder/elbow/wrist ranges.

Recommend **factorized semantic-to-deformation models** as the next architecture. Fit local, interpretable maps for shoulder orientation, elbow flexion, forearm roll, wrist orientation, and hand placement, then compose their deformation deltas through a shared full-context model with cross-factor terms, branch constraints, and an explicit residual/support estimator. Train the factors on real bilateral data and evaluate held-out actions. Keep a global supervised inverse model as a later baseline once this factorization and its out-of-support behavior are validated. Do not use local interpolation as the sole solver; the current audit shows convex mixtures fail tolerance even when drawn from the entire training set. Do not weaken support gates to make the current neighborhood pass.

The report and rerunnable audit are `tools/blender/reports/luigi_walk_joint_semantic_support_audit_v1.json` and `tools/blender/audit_joint_semantic_support.py`.
