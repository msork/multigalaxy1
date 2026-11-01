"""Blender-background test for confirm-gated native action previews."""
import os
import sys
import tempfile
from pathlib import Path

bridge_path = Path(sys.argv[-1]).resolve()
sys.path.insert(0, str(bridge_path.parent))
os.environ.setdefault("MG1_BLENDER_BRIDGE_PORT", "0")
import bpy
import bridge


def _expected(rig, mesh, action, times):
    scene = bpy.context.scene
    rig.animation_data.action = action
    rig.animation_data.action_slot = action.slots[0]
    result = []
    for time in times:
        base = int(time)
        scene.frame_set(base, subframe=time-base)
        bpy.context.view_layer.update()
        centroids = bridge._mesh_weight_centroids([mesh], [mesh], bridge._ARM_BONES["left"], rig)
        result.append({"time": time,
            "bones": {name: {"armature_pose": bridge._mat(rig.pose.bones[name].matrix),
                              "local_basis": bridge._mat(rig.pose.bones[name].matrix_basis)}
                      for name in bridge._ARM_BONES["left"]},
            "weighted_mesh_centroids": {mesh.name: {name: {"weighted_centroid_world": row["centroid_world"],
                "weighted_centroid_armature": row["centroid_armature"]}
                for name, row in centroids[mesh.name].items()}}})
    return result


def run():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    arm_data = bpy.data.armatures.new("PreviewTestRigData")
    rig = bpy.data.objects.new("Armature.004", arm_data)
    bpy.context.scene.collection.objects.link(rig)
    rig.scale = (43.437, 43.437, 43.437)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone_map = {}
    definitions = [
        ("bone_21", None, 0.0), ("bone_22", "bone_21", 1.0),
        ("bone_23", "bone_22", 2.0), ("bone_24", "bone_23", 3.0),
        ("bone_26", "bone_24", 4.0), ("bone_27", "bone_26", 5.0),
        ("bone_30", "bone_27", 6.0), ("bone_25", "bone_22", 2.0),
        ("bone_29", "bone_25", 3.0),
    ]
    for name, parent, y in definitions:
        bone = arm_data.edit_bones.new(name)
        bone.head, bone.tail = (0, y, 0), (0, y+1, 0)
        if parent:
            bone.parent = bone_map[parent]
        bone_map[name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")

    production_data = arm_data.copy()
    production = bpy.data.objects.new("123475520_waluigi.gpl", production_data)
    bpy.context.scene.collection.objects.link(production)
    production.scale = tuple(rig.scale)

    mesh_data = bpy.data.meshes.new("PreviewTestMeshData")
    mesh_data.from_pydata([(0, 1, 0), (0.2, 1, 0), (0, 1.2, 0)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new("ARMTEST_29_0x75c1640_submesh0", mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    group = mesh.vertex_groups.new(name="bone_22")
    group.add([0, 1, 2], 1.0, "REPLACE")
    modifier = mesh.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig
    bpy.context.view_layer.update()
    mesh.parent = production
    mesh.matrix_parent_inverse = production.matrix_world.inverted_safe()

    actions = []
    for action_name, radians in (("sequence_10.003", 0.0), ("sequence_15.003", 0.35), ("sequence_24", 0.7)):
        action = bpy.data.actions.new(action_name)
        slot = action.slots.new("OBJECT", rig.name)
        layer = action.layers.new("test")
        strip = layer.strips.new(type="KEYFRAME")
        bag = strip.channelbag(slot, ensure=True)
        path = rig.pose.bones["bone_22"].path_from_id("rotation_quaternion")
        q = (1.0, 0.0, 0.0, 0.0)
        q1 = (1.0, 0.0, 0.0, 0.0) if radians == 0 else (0.0, 0.0, 0.0, 1.0)
        for channel in range(4):
            curve = bag.fcurves.new(path, index=channel, group_name="bone_22")
            curve.keyframe_points.add(2)
            curve.keyframe_points[0].co = (0.25, q[channel])
            curve.keyframe_points[1].co = (1.75, q1[channel])
            curve.update()
        actions.append(action)

    rig.animation_data_create()
    expected_requests = []
    for action in actions:
        samples = _expected(rig, mesh, action, [0.25, 1.75])
        expected_requests.append({"name": action.name, "slot_identifier": action.slots[0].identifier,
                                  "samples": samples})
    scene = bpy.context.scene
    prior_frame = (scene.frame_current, scene.frame_subframe)
    result = bridge._create_native_arm_previews({"object": rig.name, "production_object": production.name,
        "meshes": [mesh.name], "actions": expected_requests, "spacing_world": 100.0})
    assert result["validation"]["rest_signature_matches_production"]
    assert result["validation"]["transform_match_within_tolerance"]
    assert result["validation"]["centroid_match_within_tolerance"]
    assert result["validation"]["preview_scales_match_source"]
    assert result["new_actions_created"] == []
    assert result["action_count"] == 3
    assert len([obj for obj in bpy.data.objects if obj.get("_mg1_native_preview_group") == result["preview_group"]]) == 6
    assert all(row["mesh_relationships"][0]["preview_parent"] == row["preview_armature"]
               for row in result["previews"])
    assert (scene.frame_current, scene.frame_subframe) == (0, 0.0)
    old_render_state = (scene.camera, scene.render.engine, scene.render.filepath,
        scene.render.image_settings.file_format, scene.render.resolution_x,
        scene.render.resolution_y, scene.render.resolution_percentage)
    with tempfile.TemporaryDirectory(prefix="mg1-preview-test-") as render_dir:
        capture = bridge._capture_native_arm_previews({"preview_group": result["preview_group"],
            "samples": {row["action"]: [sample["time"] for sample in row["samples"]]
                        for row in result["previews"]},
            "output_dir": render_dir, "resolution": 128})
        assert capture["image_count"] == 6
        assert capture["isolated_render_scene"]
        assert all(Path(path).is_file() for path in capture["images"])
        assert (scene.camera, scene.render.engine, scene.render.filepath,
            scene.render.image_settings.file_format, scene.render.resolution_x,
            scene.render.resolution_y, scene.render.resolution_percentage) == old_render_state
        assert (scene.frame_current, scene.frame_subframe) == (0, 0.0)
    bridge._restore({})
    assert not any(obj.get("_mg1_native_preview_group") == result["preview_group"] for obj in bpy.data.objects)
    assert bpy.data.objects.get(rig.name) is rig and bpy.data.objects.get(production.name) is production
    print("Native arm preview Blender test passed")


run()
