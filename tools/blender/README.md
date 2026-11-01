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

The bundle preserves exact FCurve keyframe/subframe times, local/armature/world matrices, rest-relative transforms, arm branch landmarks, static weighted vertex positions, evaluated per-bone mesh centroids, TrackIds, curve ranges, action metadata, and full slot inventory. It identifies `sequence_10.003` as the previously validated anchor for `OBArmature.004`; other slots are excluded because their source-rig link is ambiguous. The remaining `.004` actions are high-confidence family candidates; their source-owner object was not preserved, so that confidence remains an inference. The default action-level holdouts are `sequence_11.003`, `sequence_15.003`, `sequence_20.001`, `sequence_24`, and `sequence_26`.

`test_native_arm_extract_blender.py` checks exact subframe sampling, rest-relative transforms, TrackIds, weighted vertices, scene-frame restoration, and temporary-object cleanup in Blender background mode. `test_native_arm_labels.py` covers sign-invariant action feature labels.

To create the disposable viewport validation set after extracting the dataset:

```powershell
python tools/blender/preview_native_waluigi_arm.py --dataset tools/blender/reports/native_waluigi_arm_calibration.json --output tools/blender/reports/native_waluigi_preview_validation.json
```

The default three actions are `sequence_10.003`, `sequence_15.003`, and `sequence_24`. The command assigns each existing `.004` action unchanged to its own duplicate armature, duplicates both explicitly named weighted mesh objects, rebinds only those duplicate modifiers, spaces the preview characters apart, and compares sampled transforms and weighted centroids against the dataset. It also writes Workbench renders at five exact sample times per action under `tools/blender/reports/native_arm_preview_renders/`. Render setup, object visibility, camera, engine, frame, and shading settings are restored afterward. It creates no actions and refuses to run if the disposable rig's rest signature or scale differs from production. Preview objects are bridge-tagged for later targeted cleanup.
