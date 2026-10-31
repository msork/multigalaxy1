"""Run with Blender background mode: blender -b --python smoke_test.py -- bridge.py"""
import importlib.util
import pathlib
import sys

import bpy

bridge_path = pathlib.Path(sys.argv[-1]).resolve()
spec = importlib.util.spec_from_file_location("mg1_blender_bridge_test", bridge_path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

arm_data = bpy.data.armatures.new("BridgeSmokeArmData")
arm_obj = bpy.data.objects.new("BridgeSmokeArm", arm_data)
bpy.context.scene.collection.objects.link(arm_obj)
with bpy.context.temp_override(object=arm_obj, active_object=arm_obj):
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm_data.edit_bones.new("root")
    bone.tail = (0, 1, 0)
    bpy.ops.object.mode_set(mode="OBJECT")

assert bridge._inspect_armature({"object": arm_obj.name})["bones"][0]["name"] == "root"
before = bpy.context.scene.frame_current
pose = bridge._inspect_pose({"object": arm_obj.name, "frame": before + 5})
assert pose["bones"][0]["name"] == "root"
assert bpy.context.scene.frame_current == before
copy_info = bridge._duplicate_rig({"object": arm_obj.name, "name": "BridgeSmokeCopy"})
assert bpy.data.objects[copy_info["name"]].data is not arm_data
removed = bridge._restore({})
assert copy_info["name"] in removed["objects"]
assert bpy.data.objects.get(arm_obj.name) is arm_obj
bridge.stop()
print("Blender bridge smoke test passed")
