"""Run with Blender background mode: blender -b --python smoke_test.py -- bridge.py"""
import importlib.util
import os
import pathlib
import sys

import bpy

bridge_path = pathlib.Path(sys.argv[-1]).resolve()
os.environ["MG1_BLENDER_BRIDGE_PORT"] = "0"
spec = importlib.util.spec_from_file_location("mg1_blender_bridge_test", bridge_path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

arm_data = bpy.data.armatures.new("BridgeSmokeArmData")
arm_obj = bpy.data.objects.new("BridgeSmokeArm", arm_data)
bpy.context.scene.collection.objects.link(arm_obj)
arm_obj.select_set(True)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode="EDIT")
bone = arm_data.edit_bones.new("root")
bone.tail = (0, 1, 0)
child = arm_data.edit_bones.new("child")
child.head = (0, 1, 0)
child.tail = (0, 2, 0)
child.parent = bone
bpy.ops.object.mode_set(mode="OBJECT")

assert bridge._inspect_armature({"object": arm_obj.name})["bones"][0]["name"] == "root"
analysis = bridge._analyze_waluigi_arm({"object": arm_obj.name, "bones": ["child"], "frames": [1]})
assert analysis["bone_analysis"]["child"]["rest_length"] == 1.0
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
