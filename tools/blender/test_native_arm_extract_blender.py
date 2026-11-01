"""Blender-background integration test for the read-only exact-key extractor."""
import os
import sys
from pathlib import Path

bridge_path = Path(sys.argv[-1]).resolve()
sys.path.insert(0, str(bridge_path.parent))
os.environ.setdefault("MG1_BLENDER_BRIDGE_PORT", "0")
import bpy
import bridge


def run():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    arm_data = bpy.data.armatures.new("DatasetTestRigData")
    rig = bpy.data.objects.new("Armature.004", arm_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    previous = None
    names = ["bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"]
    for index, name in enumerate(names):
        bone = arm_data.edit_bones.new(name)
        bone.head = (index * 0.1, 0, 0)
        bone.tail = (index * 0.1, 1, 0)
        if previous:
            bone.parent = previous
        previous = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_data.bones["bone_22"]["TrackId"] = 16

    mesh_data = bpy.data.meshes.new("DatasetTestMeshData")
    mesh_data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new("DatasetTestMesh", mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    group = mesh.vertex_groups.new(name="bone_22")
    group.add([0, 1, 2], 1.0, "REPLACE")
    modifier = mesh.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig

    action = bpy.data.actions.new("sequence_test")
    slot = action.slots.new("OBJECT", rig.name)
    layer = action.layers.new("test")
    strip = layer.strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    path = rig.pose.bones["bone_22"].path_from_id("rotation_quaternion")
    curve = bag.fcurves.new(path, index=0, group_name="bone_22")
    curve.keyframe_points.add(2)
    curve.keyframe_points[0].co = (0.25, 1.0)
    curve.keyframe_points[1].co = (1.75, 0.9238795)
    curve.update()
    rig.animation_data_create()
    rig.animation_data.action = action
    rig.animation_data.action_slot = slot

    provenance = bridge._inspect_action_provenance({})
    action_record = next(row for row in provenance["actions"] if row["name"] == action.name)
    assert action_record["slots"][0]["identifier"] == slot.identifier
    assert action_record["assigned_armatures"][0]["object"] == rig.name
    assert len(action_record["slots"][0]["bone_names"]) == 1

    armature_info = bridge._inspect_armature({"object": rig.name})
    bone_info = next(row for row in armature_info["bones"] if row["name"] == "bone_22")
    assert armature_info["action"] == action.name
    assert armature_info["action_slot"] == slot.identifier
    assert bone_info["pose"]["rotation_mode"] == rig.pose.bones["bone_22"].rotation_mode
    assert bone_info["pose"]["matrix_basis"] == bridge._mat(rig.pose.bones["bone_22"].matrix_basis)
    assert bone_info["inherit_scale"] == rig.data.bones["bone_22"].inherit_scale

    original_frame = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
    result = bridge._extract_native_arm_dataset({
        "object": rig.name, "actions": [action.name], "include_slots": [slot.identifier],
        "meshes": [mesh.name], "holdout_actions": [],
    })
    assert result["dataset_action_count"] == 1
    assert result["sample_count"] == 2
    assert result["dataset"][0]["sample_times"] == [0.25, 1.75]
    assert result["dataset"][0]["track_ids"]["bone_22"] == 16
    assert len(result["weighted_mesh_vertices_rest"][mesh.name]["bone_22"]) == 3
    assert result["dataset"][0]["samples"][0]["bones"]["bone_22"]["rest_relative_se3"]["matrix"]
    assert not any(obj.name.startswith("_MG1_NativeArmDataset_") for obj in bpy.data.objects)
    assert (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe) == original_frame
    print("Native arm exact-key extraction Blender test passed")


run()
