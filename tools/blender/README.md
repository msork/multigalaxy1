# Blender bridge

The bridge runs in Blender and binds only to `127.0.0.1:47831`. It queues requests onto Blender's main thread. Keep the Blender session open while using the client.

## Start from Blender's Python Console

Run this from the repository root, or replace the path with the absolute path to `bridge.py`:

```python
exec(compile(open(r"tools/blender/bridge.py", encoding="utf-8").read(), "bridge.py", "exec"))
```

The console prints the bound host, port, and protocol version. Stop it from the same console with:

```python
stop()
```

## Send inspection requests

From PowerShell in the repository root:

```powershell
python tools/blender/client.py inspect.objects
python tools/blender/client.py inspect.armature --args '{"object":"123475520_waluigi.gpl"}'
python tools/blender/client.py inspect.actions
python tools/blender/client.py inspect.pose --args '{"object":"123475520_waluigi.gpl","frame":30}'
python tools/blender/client.py inspect.weights --args '{"object":"YOUR_MESH_OBJECT","groups":["bone_22","bone_23","bone_24","bone_25","bone_26","bone_27","bone_29","bone_30"]}'
python tools/blender/client.py analyze.waluigi_arm --summary --object Waluigi_Arm_Test --action-name sequence_10.003 --side left --sample-count 100 --mesh ARMTEST_29_0x75c1640_submesh0 --mesh ARMTEST_29_0x75c1640_submesh1
```

Mutation calls require `--confirm` and are limited to duplicate and bridge-owned cleanup:

```powershell
python tools/blender/client.py mutate.duplicate_rig --confirm --args '{"object":"123475520_waluigi.gpl","name":"Waluigi_Arm_Experiment"}'
python tools/blender/client.py mutate.restore --confirm
```

The first left-arm FK prototype uses source action frames 0–60, duplicates the disposable target armature and explicitly supplied meshes, bakes a new action, and saves a numerical report on the client machine:

```powershell
python tools/blender/client.py mutate.prototype_waluigi_arm --confirm --source-object luigi.001_armature --source-action luigi.001_armature_walk --target-object Waluigi_Arm_Test --target-mesh ARMTEST_29_0x75c1640_submesh0 --target-mesh ARMTEST_29_0x75c1640_submesh1 --report tools/blender/reports/waluigi_left_arm_prototype.json
```

The report records before/after armature matrices, shoulder/elbow/wrist continuity gaps, weighted mesh centroids, scale checks, and constraint counts. The duplicated preview objects and action are bridge-tagged and can be removed with the confirm-gated `mutate.restore` call after review.

Re-evaluate continuity in a saved report against its recorded baseline with:

```powershell
python tools/blender/validate_prototype_report.py tools/blender/reports/waluigi_left_arm_prototype.json
```

Cleanup only removes objects tagged by this bridge and actions tagged by this bridge. It does not remove arbitrary test objects or `sequence_*` actions. The duplicate is an armature-only copy; it is not bound to source meshes.

## Background smoke test

With Blender 5.x installed and available as `blender`:

```powershell
blender -b --python tools/blender/smoke_test.py -- tools/blender/bridge.py
```

`python -m py_compile tools/blender/bridge.py tools/blender/client.py tools/blender/rotation_math.py tools/blender/test_rotation_math.py tools/blender/test_prototype_report.py` and `python tools/blender/test_rotation_math.py` plus `python tools/blender/test_prototype_report.py` check syntax and calculations without Blender, but do not replace the Blender smoke test.

To reload a newer bridge in a session that already runs an older copy, run `stop()` in Blender's Python Console, then run the startup command above again. The running bridge does not expose arbitrary code execution or self-reload.

The arm analyzer evaluates a requested action on a temporary armature/data duplicate, samples local, armature-space, and world-space rotations, and restores the scene frame. It never assigns the action to the production object. Quaternion comparisons normalize inputs and use `abs(dot(q1, q2))`, so opposite quaternion signs produce zero angular difference. Pairwise correlation uses angular displacement sequences; relative orientation changes use evaluated armature-space rotations. Weight centroids and bounds are transformed into the analyzed armature's local space.

Run the read-only multi-action native arm study with:

```powershell
python tools/blender/study_native_arm.py --object Waluigi_Arm_Test --sample-count 41 --output tools/blender/reports/native_arm_multi_action_study.json
```

It samples five named `sequence_*` actions, reports branch-relative rotation statistics and armature-space endpoint divergence, and does not mutate Blender objects or actions.

## Native Waluigi arm calibration dataset

Reload the bridge after changing `bridge.py` by running `stop()` and then the startup command above in Blender's Python Console. The new read-only `inspect.action_provenance` operation lists Blender 5 action slots, their channel/bone signatures, and current object assignments. `extract.native_arm_dataset` evaluates only temporary duplicates and restores the scene frame.

From PowerShell, extract the `.004` native Waluigi slot family on the disposable rig, verify the test rig's rest signature against production, and hold out complete actions:

```powershell
python tools/blender/extract_native_arm_dataset.py --object Waluigi_Arm_Test --output tools/blender/reports/native_waluigi_arm_calibration.json
python tools/blender/label_native_arm_candidates.py tools/blender/reports/native_waluigi_arm_calibration.json --output tools/blender/reports/native_waluigi_arm_candidates.json
```

The bundle preserves exact FCurve keyframe/subframe times, local/armature/world matrices, rest-relative transforms, arm branch landmarks, static weighted vertex positions, evaluated per-bone mesh centroids, TrackIds, curve ranges, action metadata, and full slot inventory. The `.004` family was previously treated as high-confidence Waluigi data, but visual validation disproved direct application. Until its missing source object or importer basis is recovered, exclude all `.004` actions from training. The default action-level holdouts remain `sequence_11.003`, `sequence_15.003`, `sequence_20.001`, `sequence_24`, and `sequence_26`.

`test_native_arm_extract_blender.py` checks exact subframe sampling, rest-relative transforms, TrackIds, weighted vertices, scene-frame restoration, and temporary-object cleanup in Blender background mode. `test_native_arm_labels.py` covers sign-invariant action feature labels.

To create the disposable viewport validation set after extracting the dataset:

```powershell
python tools/blender/preview_native_waluigi_arm.py --dataset tools/blender/reports/native_waluigi_arm_calibration.json --output tools/blender/reports/native_waluigi_preview_validation.json
```

The default three actions are `sequence_10.003`, `sequence_15.003`, and `sequence_24`. The command assigns each existing `.004` action unchanged to its own duplicate armature, duplicates both explicitly named weighted mesh objects, rebinds only those duplicate modifiers, and compares sampled transforms and weighted centroids against the dataset. This is only a dataset replay check, not an independent action-source validation; the user reported detached glove/hand and incorrect arm deformation. Do not treat passing matrix or centroid checks as evidence of a correct native action. Workbench renders are written at five exact sample times per action under `tools/blender/reports/native_arm_preview_renders/` using an isolated temporary scene.

## Native action basis provenance audit

The following read-only scripts compare the live action-slot ownership and channel values with the recovered source Waluigi rest rig. In Blender background mode, create a local report from the source Sluggers blend (do not commit the report because it includes extracted rig data):

```powershell
$env:MG1_PROVENANCE_OUTPUT = "tools/blender/reports/provenance_Waluigi_Sluggers.json"
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --disable-autoexec "C:\MultiGalaxy1-Assets\Working\SMG\Waluigi_Sluggers.blend" --python tools/blender/inspect_saved_armature_provenance.py
python tools/blender/audit_native_action_basis.py --source-blend-report tools/blender/reports/provenance_Waluigi_Sluggers.json --source-glb "C:\MultiGalaxy1-Assets\Working\MSS\Waluigi\123475520_waluigi.gpl.glb"
```

The report includes only derived comparison values, action-slot provenance, and channel norms/angles; it omits raw action key values and full rest matrices. `inspect.armature` also reports object transforms, action/slot binding, bone inheritance settings, pose rotation modes/channels/basis matrices, and constraints. The original Waluigi GLB contains a 92-joint skin but no animation clips. No surviving workfile contains the `.004` action owner, so the action source basis cannot currently be proven.

## Raw Waluigi Sluggers calibration

Use the original `.sluggie` model and nine `.anm` banks through the installed
Sluggies IO add-on. This supersedes orphaned `sequence_*` actions as the native
calibration source. From the repository root with Blender 5.2.3:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --disable-autoexec --python tools/blender/extract_raw_sluggies_calibration.py
```

The extractor reads `C:\MultiGalaxy1-Assets\Working\MSS\Waluigi\`, imports
into a fresh background scene, and writes a local gzip dataset, validation
JSON, and packed-texture preview under `tools/blender/reports/`. It does not
modify a production rig or action. The validated production object matrix is
assigned to this temporary import without applying its intentional scale.
Run `python tools/blender/test_raw_anm_calibration.py` for the policy/metadata
unit tests. See
[`docs/animation-retargeting/raw-native-waluigi-calibration.md`](../../docs/animation-retargeting/raw-native-waluigi-calibration.md)
for action provenance, dataset checks, and legacy-action comparison results.

### Raw-native context PCA and Luigi walk refit

Fit the reusable target context model from the raw `.anm` dataset (never the
inferred `.004` bundle):

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python tools/blender/fit_native_context_pca.py -- --dataset tools/blender/reports/native_waluigi_raw_anm_calibration.json.gz --predictions tools/blender/reports/native_waluigi_raw_anm_context_pca_predictions.npz --report tools/blender/reports/native_waluigi_raw_anm_context_pca.json --compare-dimensions 192
```

The complete 27-action holdout identity/PCA previews can be rebuilt from the
raw-import preview blend with `build_context_pca_previews.py --raw-anm-source`.
The Luigi walk semantic fitter uses `context_dataset.py` to enforce the raw
source schema and action splits. Its current raw-model run is diagnostic only:
the saved-action semantic residuals fail consistency with the fit report. See
[`raw-anm-luigi-walk-refit.md`](../../docs/animation-retargeting/raw-anm-luigi-walk-refit.md)
before attempting another fit.
For the corrected save/reload matrix gate and remaining pre-bake elbow
discontinuity, see
[`semantic-walk-save-reload-fix.md`](../../docs/animation-retargeting/semantic-walk-save-reload-fix.md).
The joint 0–60 semantic continuity fit and v11 preview validation are recorded
in [`semantic-walk-joint-continuity.md`](../../docs/animation-retargeting/semantic-walk-joint-continuity.md).

## Direct Galaxy walk adapter probe

The direct transfer probe samples the original Luigi walk action's selected
Blender 5.x channelbag onto a disposable source control clone, measures
torso-relative shoulder/elbow/wrist trajectories, solves Waluigi segment
directions geometrically, and distributes them through the bilateral control
chain to every overlapping deform branch on a duplicate 92-bone Waluigi rig. It keeps
`Waluigi_GalaxyWalk_Legs_Baked` as the base and saves a new preview only if the
numerical checks pass. The source action, production rig, and baseline action
are not saved or modified. Because the saved validation scene's assigned
layered action stays at frame-zero pose in background mode, the probe evaluates
the original FCurves directly at integer samples; arbitrary subframe fidelity
and visual anatomy still need review.

From this worktree, run:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend' --python 'tools\blender\prototype_direct_luigi_walk.py' -- --report 'tools\blender\reports\direct_luigi_walk_v3_trajectory.json' --preview 'tools\blender\reports\direct_luigi_walk_v3_trajectory.blend'
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\direct_luigi_walk_v3_trajectory.blend' --python 'tools\blender\validate_direct_galaxy_preview.py' -- --report 'tools\blender\reports\direct_luigi_walk_v3_trajectory.json' --output 'tools\blender\reports\direct_luigi_walk_v3_trajectory_reopen.json'
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background 'tools\blender\reports\direct_luigi_walk_v3_trajectory.blend' --python 'tools\blender\analyze_direct_arm_mesh_continuity.py' -- --output 'tools\blender\reports\direct_arm_mesh_continuity.json' --frames 0 15 30 45 60
```

See [`direct-galaxy-adapter.md`](../../docs/animation-retargeting/direct-galaxy-adapter.md)
for mapping, numerical results, and current feasibility limits.
