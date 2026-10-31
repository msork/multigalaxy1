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
```

Mutation calls require `--confirm` and are limited to duplicate and bridge-owned cleanup:

```powershell
python tools/blender/client.py mutate.duplicate_rig --confirm --args '{"object":"123475520_waluigi.gpl","name":"Waluigi_Arm_Experiment"}'
python tools/blender/client.py mutate.restore --confirm
```

Cleanup only removes objects tagged by this bridge and actions tagged by this bridge. It does not remove arbitrary test objects or `sequence_*` actions. The duplicate is an armature-only copy; it is not bound to source meshes.

## Background smoke test

With Blender 5.x installed and available as `blender`:

```powershell
blender -b --python tools/blender/smoke_test.py -- tools/blender/bridge.py
```

`python -m py_compile tools/blender/bridge.py tools/blender/client.py tools/blender/smoke_test.py` checks syntax without Blender, but does not replace the smoke test.
