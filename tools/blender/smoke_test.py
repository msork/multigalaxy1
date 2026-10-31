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


def make_armature(name, bone_specs):
    data = bpy.data.armatures.new(name + "Data")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for bone_name, parent_name in bone_specs:
        bone = data.edit_bones.new(bone_name)
        bone.head = (0, len(made) * 0.2, 0)
        bone.tail = (0, len(made) * 0.2 + 0.1, 0)
        if parent_name:
            bone.parent = made[parent_name]
            bone.use_connect = True
            bone.head = bone.parent.tail
            bone.tail = (bone.head.x, bone.head.y + 0.1, bone.head.z)
        made[bone_name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


source_specs = [("Root", None), ("ShoulderL", "Root"), ("ArmL1", "ShoulderL"),
                ("ArmL2", "ArmL1"), ("HandL", "ArmL2")]
source_test = make_armature("BridgePrototypeSource", source_specs)
for source_pose in source_test.pose.bones:
    source_pose.rotation_mode = "XYZ"
source_action = bpy.data.actions.new("BridgePrototypeWalk")
source_slot = source_action.slots.new("OBJECT", source_test.name)
source_layer = source_action.layers.new("Walk")
source_strip = source_layer.strips.new(type="KEYFRAME")
source_bag = source_strip.channelbag(source_slot, ensure=True)
source_test.animation_data_create()
source_test.animation_data.action = source_action
source_test.animation_data.action_slot = source_slot
for channel_index in range(3):
    for source_bone, amount in [("ShoulderL", 0.04), ("ArmL1", 0.2), ("ArmL2", 0.15), ("HandL", -0.1)]:
        curve = source_bag.fcurves.new('pose.bones["' + source_bone + '"].rotation_euler', index=channel_index)
        curve.keyframe_points.add(2)
        curve.keyframe_points[0].co = (0, 0.0)
        curve.keyframe_points[1].co = (60, amount if channel_index == 2 else 0.0)
        curve.update()
scene = bpy.context.scene
scene.frame_set(60)
bpy.context.view_layer.update()
source_clone = source_test.copy()
source_clone.data = source_test.data.copy()
source_clone.name = "BridgePrototypeSourceClone"
bpy.context.scene.collection.objects.link(source_clone)
source_clone.animation_data_create()
source_clone.animation_data.action = source_action
source_clone.animation_data.action_slot = source_slot
bpy.context.view_layer.update()
assert source_clone.animation_data.action_slot == source_slot
assert abs(source_clone.pose.bones["ArmL1"].rotation_euler.z) > 0.19
source_clone_data = source_clone.data
bpy.data.objects.remove(source_clone, do_unlink=True)
bpy.data.armatures.remove(source_clone_data)
target_specs = [("bone_21", None), ("bone_22", "bone_21"), ("bone_23", "bone_22"),
                ("bone_24", "bone_23"), ("bone_26", "bone_24"), ("bone_27", "bone_26"),
                ("bone_30", "bone_27"), ("bone_25", "bone_22"), ("bone_29", "bone_25")]
target_test = make_armature("BridgePrototypeTarget", target_specs)
target_test.animation_data_create()
target_test.animation_data.action = source_action
target_test.animation_data.action_slot = source_slot
mesh_data = bpy.data.meshes.new("BridgePrototypeMeshData")
mesh_data.from_pydata([(0,0,0),(0,1,0),(1,0,0)], [], [(0,1,2)])
mesh_data.update()
mesh_test = bpy.data.objects.new("BridgePrototypeMesh", mesh_data)
bpy.context.scene.collection.objects.link(mesh_test)
for bone_name in ["bone_22", "bone_23", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"]:
    group = mesh_test.vertex_groups.new(name=bone_name)
    group.add([0, 1, 2], 1.0, "REPLACE")
modifier = mesh_test.modifiers.new("Armature", "ARMATURE")
modifier.object = target_test
prototype_result = bridge._prototype_waluigi_arm({
    "source_object": source_test.name, "source_action": source_action.name,
    "target_object": target_test.name, "meshes": [mesh_test.name]})
assert prototype_result["validation"]["finite_numbers"]
assert prototype_result["validation"]["no_runtime_constraints"]
assert prototype_result["after"]["action_frame_range"] == [0.0, 60.0]
assert prototype_result["after"]["runtime_constraint_count"] == 0
assert prototype_result["after"]["prototype_action"] != "Waluigi_GalaxyWalk_Legs_Baked"
assert prototype_result["validation"]["overlap_relative_motion_present"]
assert prototype_result["validation"]["bone_scales_unchanged"]
assert prototype_result["validation"]["mesh_scales_unchanged"]
assert prototype_result["validation"]["joint_continuity_within_tolerance"]
assert prototype_result["after"]["max_joint_gap_change_world"]["shoulder"] < 0.05
bridge._restore({})
bridge.stop()
print("Blender bridge smoke test passed")
