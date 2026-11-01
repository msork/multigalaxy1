# Native Waluigi Action Source Recovery

## Status

`Armature.001` is now the candidate action-owner basis for conversion
experiments. The user confirmed that `sequence_10.003` is visually coherent on
its saved mesh-bound rig. This establishes a useful Blender source basis, but
it still does **not** identify the raw `.anm` bank or prove that
`Armature.001` was the original game-import owner. Keep these actions out of
manifold fitting/training until their raw bank and TrackIds are recovered.

## Evidence recovered

- Read-only inspection of
  `%LOCALAPPDATA%\Temp\Waluigi_Sluggers_2368_autosave.blend` found 11 armatures
  and 81 actions. The file remains outside Git and was not saved over.
- The autosave has an object named `Armature.004`, data `Armature.005`, 92
  bones, identity object transform, and rest signature prefix `5f6b4bf095`.
  It has `sequence_0.004` assigned in slot `OBArmature.004`.
- The same autosave has `Armature.001`, data `Armature.001`, rest signature
  prefix `ca08a03beb`, uniform object scale 43.437, and
  `sequence_6.004` assigned in slot `OBArmature.004`. The two Waluigi submesh
  objects are parented to this armature, and `submesh0` has an armature
  modifier targeting it. The slot is thus reused across different objects;
  its identifier names a prior Blender target but does not establish the
  current or original action owner.
- `sequence_10.003` is in the autosave and also uses slot
  `OBArmature.004`. Its Blender action metadata is empty. The inspected
  `sequence_*` actions do not have the Sluggies add-on's
  `SluggiesAnmSource`, group, bank, or sequence metadata. The armature bone
  custom properties also do not contain the add-on's `track_id` values.
- The saved `Working/SMG/Waluigi_Sluggers.blend` is the clean model import, not
  an animation source: it has the production Waluigi rig with rest signature
  `6a5f499235`, scale 43.437, and no `sequence_*` actions. Its production rest
  basis differs from the `Armature.004` candidate (`5f6b4bf095`) and from the
  mesh-bound `Armature.001` (`ca08a03beb`). These distinct rest signatures
  rule out treating the saved model rig as interchangeable with the autosave
  action rigs without a verified conversion.
- The extracted Sluggies Blender add-on `SluggiesIO_BlenderAddon_v0.9.0.zip`
  documents and implements `.anm` import. It maps ANM tracks by the
  `.sluggie` bone `track_id`, treats keys as game-local SRT, and—when the
  `SluggiesBonesReoriented` marker applies—converts with per-bone game-rest
  corrections. Its imported action names are `<bank>_seq<index>` and it stores
  source path, bank magic, sequence index/count, and original track records.
  The autosave's generic `sequence_N.*` actions and absent metadata therefore
  do not match this add-on's normal import fingerprint. This is evidence of a
  different or metadata-stripped path, not proof of which one.
- The available extracted game data has `dt_na.dat` but no extracted Waluigi
  `.anm` files. No source bank path or raw track IDs could be recovered from
  the autosave. The action FCurves are keyed by `bone_N` names, which alone do
  not prove the game's TrackId mapping.

## Disposable reconstruction

`tools/blender/reconstruct_native_action_preview.py` opens a specified saved
scene, duplicates the selected rig and its directly parented meshes, copies
the action into a new action, and writes a separate `.blend` plus a diagnostic
JSON. It does not save the input file. The generated local preview used:

- input: `%LOCALAPPDATA%\Temp\Waluigi_Sluggers_2368_autosave.blend`
- source armature/mesh owner: `Armature.001` (chosen because the surviving
  Waluigi submeshes are bound to it)
- action: `sequence_10.003`, slot `OBArmature.004`
- disposable output objects: `RecoveredNativePreview_Rig`,
  `RecoveredNativePreview_submesh0`, `RecoveredNativePreview_submesh1`
- copied action: `RecoveredNativePreview_sequence_10.003`

This is a direct replay on the saved mesh-bound rig basis, not a guessed
conversion to the production rig. The user visually confirmed this action as
a coherent baseball throw on the duplicate.

## Candidate-owner to production-basis conversion prototype

`tools/blender/compare_native_owner_basis.py` compares `Armature.001` and the
production `123475520_waluigi.gpl` object in the same autosave. It confirms
92 matching bone names and identical parent hierarchy. Their object scales
are both the intentional `43.437`; the candidate object has no rotation,
while production has a 90-degree X rotation. The constant coordinate change
from candidate armature space to production armature space is
`C = inverse(M_production) @ M_candidate`, which evaluates to an X rotation
of `-90 degrees` (up to Blender float precision), with no scale component.

The bone rest frames are **not** one global rotation apart. Candidate and
production arm-bone head locations match in world space within `0.00009`, but
tail locations differ by up to `33.386` world units; rest axis/orientation
differences are about `90 degrees` on bones 22–29 and about `121.3 degrees` on
bone 30. The rest lengths of bones 22–27 match closely, while `bone_29` is
`0.54349` candidate vs `0.21083` production and `bone_30` is `0.26466` vs
`0.32383`. The saved candidate pose channels are non-identity after its
previous action state is removed; production's saved neutral channels are
identity. Conversion therefore clears candidate and target duplicate pose
bases before evaluating each action.

For each bone `i`, let `R_c,i` and `R_p,i` be its candidate and production
armature-space rest matrices, and `P_c,i(t)` its evaluated candidate pose
matrix. A constant per-bone correction is
`K_i = inverse(R_c,i) @ inverse(C) @ R_p,i`. The converted production pose is:

```text
P_p,i(t) = C @ P_c,i(t) @ K_i
```

This preserves the source skinning delta in the changed armature coordinate
frame while restoring each production bone's own rest matrix. Target local
`matrix_basis` channels are then derived top-down against production parent
pose/rest matrices. The algorithm is constant per bone; `K_i` depends only on
the two rigs' rest matrices, and `C` only on object transforms. It does not
assume rigidly coupled arm branches or copy source quaternion channels.

`tools/blender/convert_native_owner_actions.py` baked the full 92-bone pose
for three actions onto a duplicate of the production-basis rig and its
production mesh. It sampled all original keyed times plus the midpoint of
each interval, converted matrix poses to TRS channels, and replayed the written
actions. No action was assigned to or written on the production object.

| Source action | Original keys | Bake samples | Pose matrix error | Same-mesh max vertex error | Weighted centroid motion error |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sequence_10.003` | 83 | 165 | `4.1e-6` | `0.000183` | `0.000151` |
| `sequence_15.003` | 31 | 61 | `3.9e-6` | `0.000206` | `0.000185` |
| `sequence_24` | 63 | 125 | `3.6e-6` | `0.000197` | `0.000190` |

The vertex and centroid checks compare the actual production mesh and weights
on both rigs: a temporary source-space comparison mesh is made by transforming
the production vertices **and shape keys** by `inverse(C)`. This avoids
confounding different exported mesh topology with the deformation test. TRS
decomposition error stayed below `1.5e-6`; written-action basis replay error
stayed below `1.2e-6`. The duplicate retained scale `43.437` and had zero
object or pose constraints. Converted elbow, overlap-elbow, and wrist/glove
endpoint gaps remained below `0.00014`; the shoulder retains the existing
`6.138` world-unit baseline offset with only about `0.12` difference from the
candidate snapshot.

Rendered midpoint comparisons for all three actions showed the converted
production mesh retaining a connected shoulder/arm/glove silhouette matching
the candidate native action. The generated local preview is
`tools/blender/reports/Waluigi_Converted_Native_Actions.blend`; its target rig
is `NativeConverted_ProductionBasis`, with actions
`NativeConverted_sequence_10.003`, `NativeConverted_sequence_15.003`, and
`NativeConverted_sequence_24`. Rendered comparison images are left in the
same reports folder locally and are not committed.

## Next recovery step

Obtain the exact Sluggers animation bank(s) for Waluigi from the game's
`anm` folder (banks 6–14 per the Sluggies guide), then import them onto a
fresh Waluigi `.sluggie` armature using the documented Sluggies `.anm` importer.
Preserve the original `.anm`, imported action metadata, bone `track_id`
properties, and the same `.sluggie` rest basis together. Compare each imported
action against the generic `sequence_*` channels and slot. Only a match with
the expected source metadata and successful native mesh deformation should
promote the generic actions to trusted data. No conversion matrix should be
inferred from the current autosave alone.
