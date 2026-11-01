# Native Waluigi Action Source Recovery

## Status

The current evidence is sufficient to locate a candidate native animation
scene and replay one action on a disposable copy. It is **not sufficient to
prove that `Armature.004` is the original action owner or to identify the raw
`.anm` bank**. Do not use the orphaned `sequence_*` actions as calibration or
training data until those provenance gaps are resolved.

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
conversion to the production rig. It only establishes that the saved Blender
action can be assigned and evaluated on a duplicate. Visual continuity still
requires a human viewport check of shoulder, elbow, forearm, wrist, glove, and
fingers over the action; no visual-pass claim is made by the dataset preview
numerics.

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
