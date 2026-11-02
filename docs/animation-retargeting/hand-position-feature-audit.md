# Hand-position semantic feature audit

## Decision

The shared torso-frame `hand_delta_x` feature is retired. Its single lateral
component confounded outward motion with rig-specific torso and shoulder
orientations. The source walk had 35/61 unsupported left frames and 23/61
unsupported right frames.

Use three signed, dimensionless coordinates: `hand_radial`,
`hand_tangential`, and `hand_depth`. For each side and rig, the extractor:

1. Measures the rest shoulder-to-hand vector in the rest-calibrated anatomical
   frame.
2. Uses its normalized direction as radial; projects anatomical forward onto
   the perpendicular plane for tangent; uses their cross product for depth.
3. Projects the animated shoulder-relative hand displacement minus its rest
   displacement onto those fixed axes.
4. Divides by the rest shoulder-to-elbow plus elbow-to-wrist length.

This preserves sign and direction, normalizes the Luigi/Waluigi arm-length
difference, and does not let the animated shoulder frame redefine the feature
axes. The same `semantic_features` function is used by the Luigi source audit,
the raw Waluigi extractor, and future fitting code. Raw calibration semantics
are recomputed from stored poses during coverage audits so cached legacy
`hand_delta_x` vectors cannot bias the result.

## Alternatives on Luigi walk 0–60 vs 4,116 Waluigi training samples

The support threshold is 0.08 normalized units (or an exact sign class for the
split-sign alternative). The gate requires at least 55/61 supported frames
for every retained component on each side.

| Encoding | Left unsupported | Right unsupported | p95 nearest error (L / R) | Result |
| --- | ---: | ---: | ---: | --- |
| Torso-frame signed lateral `hand_delta_x` | 35/61 | 23/61 | 0.52811 / 0.36155 | Fail |
| Shoulder-local signed lateral | 0/61 | 0/61 | 0.00057 / 0.00070 | Pass, but discards other directions |
| Absolute normalized shoulder-hand lateral | 0/61 | 0/61 | 0.00310 / 0.00469 | Reject: loses rest-relative displacement |
| Signed arm-plane radial/tangential/depth | 0/61 in each of 3 channels | 0/61 in each of 3 channels | worst channel 0.00173 / 0.00107 | **Selected** |
| Unsigned reach + outward sign + tangential | sign: 37/61 | sign: 27/61 | continuous p95 0.00160 / 0.00173 | Fail: direction class is unsupported |

The arm-plane coordinates correlate with the visible hand path and step rather
than discarding that trajectory: for example, radial vs normalized hand-step
length is `0.780` left and `0.721` right. Tangential and depth have distinct
signed correlations on each side; this supports retaining all three channels
rather than collapsing them to reach magnitude. The full correlation table is
in `tools/blender/reports/luigi_walk_hand_lateral_feature_audit_v1.json`.

## Coverage result and limits

The read-only bilateral audit recomputes Luigi and Waluigi semantics using the
same rest-calibrated coordinates. All retained hand-position components meet
the 90% support gate on both sides. The forearm-roll implementation was also
made torso-relative after a local fit exposed that it alone counted shared
torso rotation as arm roll. The v3 bilateral audit confirms all 11 dimensions
remain within the scalar coverage gate. This does not establish that their
full 11D combinations are jointly supported by a local native neighborhood;
the first local fit failed that stricter check. See
`luigi_walk_bilateral_semantic_coverage_v3_torso_neutral_roll.json` and
`local-native-luigi-walk-results.md`.

The stored bilateral dataset predates this feature schema and intentionally
remains unchanged. To rebuild its `anatomical_semantics` fields in the future,
run `extract_raw_sluggies_calibration.py`; it now records the feature names and
arm-plane convention in the dataset metadata.
