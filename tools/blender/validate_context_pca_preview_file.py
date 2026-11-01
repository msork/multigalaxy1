"""Validate a saved native/PCA overlay after reopening its .blend file."""
import argparse
import json
import os
import sys

import bpy


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-overlay-error", type=float, default=0.01)
    return parser.parse_args(argv)


def action_slot(action):
    if not action.slots:
        raise RuntimeError(f"{action.name}: missing action slot")
    slot = action.slots[0]
    return next((bag for layer in action.layers for strip in layer.strips
                 for bag in getattr(strip, "channelbags", []) if bag.slot == slot), None)


def action_times(action):
    bag = action_slot(action)
    if bag is None:
        raise RuntimeError(f"{action.name}: missing channelbag")
    keys = sorted({float(point.co.x) for curve in bag.fcurves
                   if curve.data_path.startswith("pose.bones[")
                   for point in curve.keyframe_points})
    times = set(keys + [float(value) for value in action.frame_range])
    times.update((a+b)*0.5 for a, b in zip(keys, keys[1:]))
    return sorted(times)


def mesh_vertices(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def world_matrix(obj):
    return [[float(obj.matrix_world[r][c]) for c in range(4)] for r in range(4)]


def main():
    args = parse_args()
    scene = bpy.context.scene
    native_collections = [collection for collection in bpy.data.collections
                          if collection.name.startswith("NativeReference_")]
    pca_collections = [collection for collection in bpy.data.collections
                       if collection.name.startswith("PCAOverlay_")]
    if len(native_collections) != 1 or len(pca_collections) != 1:
        raise RuntimeError("expected exactly one native and one PCA preview collection")
    native_collection, pca_collection = native_collections[0], pca_collections[0]
    native_collection.hide_viewport = False
    pca_collection.hide_viewport = False
    bpy.context.view_layer.update()

    native_rig = next(obj for obj in native_collection.objects if obj.type == "ARMATURE")
    pca_rig = next(obj for obj in pca_collection.objects if obj.type == "ARMATURE")
    native_action = native_rig.animation_data.action
    pca_action = pca_rig.animation_data.action
    native_rig.animation_data.action_slot = native_action.slots[0]
    pca_rig.animation_data.action_slot = pca_action.slots[0]

    native_meshes = {obj.name.replace(native_collection.name + "_", "", 1): obj
                     for obj in native_collection.objects if obj.type == "MESH"}
    pca_meshes = {obj.name.replace(pca_collection.name + "_", "", 1): obj
                  for obj in pca_collection.objects if obj.type == "MESH"}
    if native_meshes.keys() != pca_meshes.keys():
        raise RuntimeError("native/PCA mesh object sets differ")
    times = sorted(set(action_times(native_action)) | set(action_times(pca_action)))
    worst = {"error_world": 0.0}
    sq, count = 0.0, 0
    profile_max = nonprofile_max = rig_world_max = mesh_world_max = 0.0
    profile = {f"bone_{index}" for index in (1, 2, 3, 5, 6, 19, 20, 21, 22, 23, 24, 25,
        26, 27, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
        46, 47, 48, 49, 50, 51, 52)}
    for time in times:
        whole = int(time // 1)
        scene.frame_set(whole, subframe=float(time - whole))
        bpy.context.view_layer.update()
        rig_world_max = max(rig_world_max, max(abs(native_rig.matrix_world[r][c] -
            pca_rig.matrix_world[r][c]) for r in range(4) for c in range(4)))
        native_pose = native_rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
        pca_pose = pca_rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
        for name in native_pose.keys():
            error = max(abs(native_pose[name].matrix[r][c] - pca_pose[name].matrix[r][c])
                        for r in range(4) for c in range(4))
            if name in profile:
                profile_max = max(profile_max, error)
            else:
                nonprofile_max = max(nonprofile_max, error)
        for mesh_key in native_meshes:
            native_obj, pca_obj = native_meshes[mesh_key], pca_meshes[mesh_key]
            mesh_world_max = max(mesh_world_max, max(abs(native_obj.matrix_world[r][c] -
                pca_obj.matrix_world[r][c]) for r in range(4) for c in range(4)))
            a, b = mesh_vertices(native_obj), mesh_vertices(pca_obj)
            if len(a) != len(b):
                raise RuntimeError(f"vertex count differs for {mesh_key}")
            for index, (point_a, point_b) in enumerate(zip(a, b)):
                error = float((point_a - point_b).length)
                sq += error * error
                count += 1
                if error > worst["error_world"]:
                    worst = {"error_world": error, "time": time, "mesh": mesh_key,
                             "vertex": index}
    report = {"schema": "mg1.native-arm-context-pca-saved-preview-validation.v1",
        "file": bpy.data.filepath, "native_collection": native_collection.name,
        "pca_collection": pca_collection.name, "native_rig": native_rig.name,
        "pca_rig": pca_rig.name, "native_action": native_action.name,
        "pca_action": pca_action.name, "mesh_objects": sorted(native_meshes),
        "sample_count": len(times), "vertex_count_per_sample": count // max(1, len(times)),
        "max_full_character_overlay_vertex_error_world": worst["error_world"],
        "rmse_full_character_overlay_vertex_error_world": (sq / max(1, count)) ** 0.5,
        "worst_overlay_vertex": worst, "max_profile_pose_matrix_error": profile_max,
        "max_nonprofile_native_pose_matrix_error": nonprofile_max,
        "max_rig_world_matrix_error": rig_world_max,
        "max_mesh_world_matrix_error": mesh_world_max,
        "overlay_gate": "pass" if worst["error_world"] <= args.max_overlay_error else "fail",
        "overlay_tolerance_world": args.max_overlay_error}
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("MG1_SAVED_PREVIEW_VALIDATION=" + json.dumps(report))
    if report["overlay_gate"] != "pass":
        raise RuntimeError("saved full-character overlay exceeds tolerance")


if __name__ == "__main__":
    main()
