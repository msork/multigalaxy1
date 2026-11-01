"""Build and validate a zero-change semantic preview against the walk action.

Run in Blender background mode on a scene containing the production Waluigi
rig, its bound meshes, and Waluigi_GalaxyWalk_Legs_Baked. Only duplicates are
animated or saved. The solver is not imported or executed.
"""
import argparse
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_context_pca_previews import (
    associated_objects, duplicate_character_objects, move_to_collection,
)
from convert_native_owner_actions import duplicate_rig


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--walk-slot", default="")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=60)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def rows(matrix):
    return [[float(matrix[r][c]) for c in range(4)] for r in range(4)]


def max_matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def active_layer_for(collection):
    def visit(layer):
        if layer.collection == collection:
            layer.exclude = False
            layer.hide_viewport = False
            return True
        for child in layer.children:
            if visit(child):
                layer.exclude = False
                layer.hide_viewport = False
                return True
        return False
    return visit(bpy.context.view_layer.layer_collection)


def rebind_action_curves(action, source_slot, target):
    """Copy one slot's channels to a duplicate-owned local action slot."""
    source_bags = [bag for layer in action.layers for strip in layer.strips
                   for bag in strip.channelbags if bag.slot == source_slot]
    if len(source_bags) != 1:
        raise RuntimeError("expected exactly one source walk channelbag")
    duplicate = bpy.data.actions.new("_NoOpSource_" + target.name)
    slot = duplicate.slots.new("OBJECT", target.name)
    layer = duplicate.layers.new("Rebound validated walk channels")
    strip = layer.strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    for source_curve in source_bags[0].fcurves:
        group = source_curve.group.name if source_curve.group else None
        options = {"index": source_curve.array_index}
        if group:
            options["group_name"] = group
        curve = bag.fcurves.new(source_curve.data_path, **options)
        curve.keyframe_points.add(len(source_curve.keyframe_points))
        for source_point, point in zip(source_curve.keyframe_points, curve.keyframe_points):
            point.co = source_point.co
            point.interpolation = source_point.interpolation
            point.handle_left_type = source_point.handle_left_type
            point.handle_right_type = source_point.handle_right_type
            point.handle_left = source_point.handle_left
            point.handle_right = source_point.handle_right
        curve.update()
    target.animation_data_create()
    target.animation_data.action = duplicate
    target.animation_data.action_slot = slot
    return duplicate


def main():
    args = parse_args()
    if args.end < args.start:
        raise ValueError("end frame precedes start frame")
    scene = bpy.context.scene
    target = bpy.data.objects.get(args.target_rig)
    walk = bpy.data.actions.get(args.walk_action)
    if not target or target.type != "ARMATURE" or walk is None:
        raise RuntimeError("production-basis Waluigi rig or validated walk action is missing")
    slots = [slot for slot in walk.slots
             if not args.walk_slot or slot.identifier == args.walk_slot]
    if len(slots) != 1:
        raise RuntimeError("validated walk action slot is ambiguous")
    slot = slots[0]
    if abs(float(target.scale.x) - 43.437) > 0.01:
        raise RuntimeError("Waluigi source object scale differs from the expected 43.437")

    source_objects = associated_objects(target)
    source_objects = [obj for obj in source_objects if obj.type == "MESH" and
        not obj.name.startswith("ARMTEST_") and "_ArmRetargetPreview" not in obj.name]
    if not source_objects:
        raise RuntimeError("no production Waluigi meshes found")
    old_frame = scene.frame_current
    old_subframe = scene.frame_subframe
    original_objects = set(bpy.data.objects)
    original_actions = set(bpy.data.actions)
    original_collections = set(bpy.data.collections)
    reference_collection = bpy.data.collections.new("SemanticNoOp_Reference")
    preview_collection = bpy.data.collections.new("SemanticNoOp_Preview")
    scene.collection.children.link(reference_collection)
    scene.collection.children.link(preview_collection)
    rigs, meshes = [], []
    actions = []
    try:
        reference_rig = duplicate_rig(target, "SemanticNoOp_Reference_Rig")
        preview_rig = duplicate_rig(target, "SemanticNoOp_Preview_Rig")
        rigs.extend((reference_rig, preview_rig))
        move_to_collection(reference_rig, reference_collection)
        move_to_collection(preview_rig, preview_collection)
        reference_meshes = duplicate_character_objects(source_objects, target,
            reference_rig, reference_collection, "SemanticNoOp_Reference_")
        preview_meshes = duplicate_character_objects(source_objects, target,
            preview_rig, preview_collection, "SemanticNoOp_Preview_")
        # Match the already visually validated PCA preview: the 43.437 object
        # transform lives on the armature, and bound meshes have unit world
        # scale. Preserving the autosave's world-scale-1 mesh while adding a
        # scaled parent creates a 43.437x different modifier-space transform.
        for obj in reference_meshes + preview_meshes:
            obj.matrix_world = target.matrix_world.copy()
        meshes.extend(reference_meshes + preview_meshes)
        frames = list(range(args.start, args.end + 1))
        # Copy the original ActionSlot curves into actions owned by the two
        # duplicates. Reusing the source slot by name is known to bind
        # incompletely; reconstructing armature-space matrices is also avoided.
        reference_action = rebind_action_curves(walk, slot, reference_rig)
        reference_action.name = "Waluigi_LuigiWalk_SemanticNoOp_Reference_v1"
        actions.append(reference_action)
        preview_action = rebind_action_curves(walk, slot, preview_rig)
        preview_action.name = "Waluigi_LuigiWalk_SemanticNoOp_v1"
        actions.append(preview_action)

        # Keep only the no-op preview visible in the generated file.
        reference_collection.hide_viewport = True
        reference_collection.hide_render = True
        preview_collection.hide_viewport = False
        preview_collection.hide_render = False
        if not active_layer_for(preview_collection):
            raise RuntimeError("no-op preview collection is not in the active view layer")
        if not active_layer_for(preview_collection):
            raise RuntimeError("preview collection is missing from the active layer")
        reference_collection.hide_viewport = True
        reference_collection.hide_render = True
        for obj in list(reference_collection.objects):
            obj.hide_viewport = True
            obj.hide_render = True
            obj.hide_set(True)
        for obj in list(bpy.data.objects):
            if obj in original_objects:
                obj.hide_set(True)
        for obj in [preview_rig] + preview_meshes:
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_set(False)
        bpy.context.view_layer.objects.active = preview_rig
        preview_rig.select_set(True)
        scene.frame_set(args.start)
        bpy.context.view_layer.update()

        report = {
            "schema": "mg1.semantic-preview-noop-validation.v1",
            "target_rig": target.name, "walk_action": walk.name,
            "walk_slot": slot.identifier, "frames": frames,
            "source_mesh_objects": [obj.name for obj in source_objects],
            "preview_mesh_objects": [obj.name for obj in preview_meshes],
            "reference_mesh_objects": [obj.name for obj in reference_meshes],
            "preview": {"file": os.path.abspath(args.preview),
                "collection": preview_collection.name, "rig": preview_rig.name,
                "root": None, "action": preview_action.name},
            "rig_world": rows(preview_rig.matrix_world),
            "mesh_binding": [{"object": obj.name,
                "parent": obj.parent.name if obj.parent else None,
                "matrix_parent_inverse": rows(obj.matrix_parent_inverse),
                "matrix_world": rows(obj.matrix_world),
                "armature_modifiers": [{"object": mod.object.name if mod.object else None,
                    "use_vertex_groups": mod.use_vertex_groups,
                    "use_bone_envelopes": mod.use_bone_envelopes,
                    "use_deform_preserve_volume": mod.use_deform_preserve_volume}
                    for mod in obj.modifiers if mod.type == "ARMATURE"]}
                for obj in preview_meshes],
            "generation_status": "saved; run validate_semantic_noop_saved.py in a fresh process",
            "production_mutated": False}

        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
        print("MG1_SEMANTIC_PREVIEW_NOOP=" + json.dumps(report))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        # Do not save changes to the source scene; clean only the disposable IDs.
        for obj in list(bpy.data.objects):
            if obj in original_objects:
                continue
            obj_type, data = obj.type, obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if obj_type == "MESH":
                    bpy.data.meshes.remove(data)
                elif obj_type == "ARMATURE":
                    bpy.data.armatures.remove(data)
        for action in list(bpy.data.actions):
            if action not in original_actions:
                bpy.data.actions.remove(action, do_unlink=True)
        for collection in list(bpy.data.collections):
            if collection not in original_collections:
                bpy.data.collections.remove(collection)


if __name__ == "__main__":
    main()
