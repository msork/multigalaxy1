"""Evaluate fitted arm models on every dataset sample and create holdout previews.

The input Blender file is never saved. Source/target rigs and production meshes
are copied; all generated actions use unique experiment names.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_converted_native_arm_dataset import mesh_centroids
from convert_native_owner_actions import duplicate_rig, make_action, set_frame


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output-blend", required=True)
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--preview-actions", nargs="*", default=None)
    return parser.parse_args(argv)


def load_json_gzip(path):
    import gzip
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def reset_pose(rig):
    rig.animation_data_create()
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()


def duplicate_meshes(sources, rig, prefix):
    copies = []
    for source in sources:
        dup = source.copy()
        dup.data = source.data.copy()
        dup.name = prefix + source.name
        dup.data.name = dup.name + "_Data"
        bpy.context.scene.collection.objects.link(dup)
        world = source.matrix_world.copy()
        dup.parent = rig
        dup.matrix_world = world
        for modifier in dup.modifiers:
            if modifier.type == 'ARMATURE':
                modifier.object = rig
        copies.append(dup)
    return copies


def decoded_pose_matrices(feature, dataset, bones):
    output = {}
    for index, name in enumerate(bones):
        start = index * 6
        translation = Vector(tuple(float(v) for v in feature[start:start + 3]))
        rotvec = np.asarray(feature[start + 3:start + 6], dtype=np.float64)
        angle = float(np.linalg.norm(rotvec))
        if angle < 1e-10:
            quat = Quaternion()
        else:
            quat = Quaternion(Vector((float(v) for v in rotvec / angle)), angle)
        delta = quat.to_matrix().to_4x4()
        delta.translation = translation
        rest = Matrix(dataset["rest_bones"][name]["rest_matrix"])
        output[name] = rest @ delta
    return output


def hierarchy_depth(bone):
    depth = 0
    while bone.parent:
        depth += 1
        bone = bone.parent
    return depth


def assign_pose_matrices(rig, pose_matrices):
    # Matrix assignment is parent-space aware; parent bones are written first.
    for name in sorted(pose_matrices, key=lambda n: hierarchy_depth(rig.data.bones[n])):
        rig.pose.bones[name].matrix = pose_matrices[name]
        bpy.context.view_layer.update()
    return {name: rig.pose.bones[name].matrix_basis.copy() for name in pose_matrices}


def mesh_reference_centroids(sample, bones):
    output = {}
    for mesh_name, rows in sample["weighted_mesh_centroids"].items():
        output[mesh_name] = {bone: (None if rows[bone]["weighted_centroid_world"] is None else
            np.asarray(rows[bone]["weighted_centroid_world"], dtype=np.float64)) for bone in bones}
    return output


def centroid_errors(measured, reference, bones):
    errors = {bone: [] for bone in bones}
    for mesh_name, rows in measured.items():
        ref_rows = reference.get(mesh_name)
        if ref_rows is None:
            continue
        for bone in bones:
            actual = rows[bone]["weighted_centroid_world"]
            expected = ref_rows.get(bone)
            if actual is not None and expected is not None:
                errors[bone].append(float((np.asarray(actual) - expected).dot(np.asarray(actual) - expected) ** 0.5))
    return errors


def summarize_centroids(rows):
    result = {}
    for split in ("train", "holdout"):
        selected = [row for row in rows if row["split"] == split]
        bones = sorted({bone for row in selected for bone in row["per_bone"]})
        per_bone = {}
        for bone in bones:
            values = [(v, n) for row in selected if bone in row["per_bone"]
                      for v, n in [(row["per_bone"][bone]["rmse"], row["per_bone"][bone]["sample_count"])] if n]
            count = sum(n for _, n in values)
            per_bone[bone] = {"rmse_world": float(math.sqrt(sum(v*v*n for v, n in values) / count)) if count else None,
                              "max_world": float(max((row["per_bone"][bone]["max"] for row in selected if bone in row["per_bone"]), default=0.0)),
                              "sample_count": count}
        result[split] = {"action_count": len(selected), "per_bone": per_bone,
                         "worst_bone": max((b for b in bones if per_bone[b]["rmse_world"] is not None),
                             key=lambda b: per_bone[b]["rmse_world"], default=None)}
    return result


def main():
    args = parse_args()
    dataset = load_json_gzip(args.dataset)
    bones = list(dataset["bones"])
    action_data = dataset["actions"]
    selected_preview = set(args.preview_actions or dataset.get("holdout_actions", []))
    if not selected_preview:
        selected_preview = {a["action"] for a in action_data if a["split"] == "holdout"}
    if not selected_preview.issubset({a["action"] for a in action_data if a["split"] == "holdout"}):
        raise ValueError("preview actions must be members of the holdout split")
    target_original = bpy.data.objects.get(args.target_rig)
    if not target_original or target_original.type != 'ARMATURE':
        raise RuntimeError("production-basis target rig is missing")
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH' and
              any(mod.type == 'ARMATURE' and mod.object == target_original for mod in obj.modifiers)]
    if not meshes:
        raise RuntimeError("no production-basis arm-weighted mesh was found")
    predictions = np.load(args.predictions, allow_pickle=False)
    pred_maps = {"PCA": {}, "RBF": {}}
    for model in pred_maps:
        names = sorted(k for k in predictions.files if k.startswith(model + "__name__"))
        for name_key in names:
            idx = name_key.split("__")[-1]
            action_name = str(predictions[name_key].item())
            pred_maps[model][action_name] = predictions[f"{model}__{idx}"].astype(np.float64)

    scene = bpy.context.scene
    previous_frame, previous_subframe = scene.frame_current, scene.frame_subframe
    old_objects = {obj.name for obj in bpy.data.objects}
    old_actions = {action.name for action in bpy.data.actions}
    rigs, mesh_copies, created_actions = {}, {}, []
    centroid_report = {}
    try:
        for model in ("PCA", "RBF"):
            rig = duplicate_rig(target_original, "Exploratory_" + model + "_Arm_Rig")
            rigs[model] = rig
            mesh_copies[model] = duplicate_meshes(meshes, rig, "Exploratory_" + model + "_")
            reset_pose(rig)
            centroid_actions = []
            for action in action_data:
                name = action["action"]
                pred = pred_maps[model][name]
                if len(pred) != len(action["samples"]):
                    raise ValueError(f"prediction/sample count mismatch in {model}/{name}")
                basis_samples, per_sample_errors = [], []
                for i, sample in enumerate(action["samples"]):
                    set_frame(scene, float(sample["time"]))
                    matrices = decoded_pose_matrices(pred[i], dataset, bones)
                    bases = assign_pose_matrices(rig, matrices)
                    if action["split"] == "holdout" and name in selected_preview:
                        basis_samples.append((float(sample["time"]), bases))
                    depsgraph = bpy.context.evaluated_depsgraph_get()
                    measured = mesh_centroids(mesh_copies[model], depsgraph, bones)
                    measured = {source.name: measured[copy.name]
                                for source, copy in zip(meshes, mesh_copies[model])}
                    reference = mesh_reference_centroids(sample, bones)
                    errors = centroid_errors(measured, reference, bones)
                    per_sample_errors.append(errors)
                per_bone = {}
                for bone in bones:
                    vals = [values[bone][0] for values in per_sample_errors if values[bone]]
                    per_bone[bone] = {"rmse": float(np.sqrt(np.mean(np.square(vals)))) if vals else None,
                                      "max": float(max(vals)) if vals else 0.0,
                                      "sample_count": len(vals)}
                centroid_actions.append({"action": name, "split": action["split"], "per_bone": per_bone})
                if action["split"] == "holdout" and name in selected_preview:
                    converted = make_action(rig, f"Exploratory_{model}_{name}", basis_samples)
                    created_actions.append(converted)
                    rig.animation_data.action = None
                    reset_pose(rig)
            centroid_report[model] = summarize_centroids(centroid_actions)
            centroid_report[model]["per_action"] = centroid_actions

        # Temporary, clearly named preview rigs are visually separated while
        # retaining their 43.437 object scales and all source transforms.
        rigs["PCA"].matrix_world.translation += Vector((-120.0, 0.0, 0.0))
        rigs["RBF"].matrix_world.translation += Vector((120.0, 0.0, 0.0))
        bpy.context.view_layer.update()
        for model in ("PCA", "RBF"):
            first = sorted(selected_preview)[0]
            action = bpy.data.actions.get(f"Exploratory_{model}_{first}")
            if action:
                rigs[model].animation_data.action = action
                if action.slots:
                    rigs[model].animation_data.action_slot = action.slots[0]
        first_action = next(a for a in action_data if a["action"] == sorted(selected_preview)[0])
        scene.frame_set(int(math.floor(first_action["samples"][0]["time"])))

        with open(args.report, "r", encoding="utf-8") as stream:
            report = json.load(stream)
        report["weighted_mesh_centroid_reconstruction"] = centroid_report
        report["preview"] = {"blend": os.path.abspath(args.output_blend),
            "models": {model: {"rig": rigs[model].name,
                "meshes": [obj.name for obj in mesh_copies[model]],
                "actions": [f"Exploratory_{model}_{name}" for name in sorted(selected_preview)],
                "object_scale": [float(v) for v in rigs[model].scale]} for model in ("PCA", "RBF")},
            "visual_review_required": True}
        with open(args.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.makedirs(os.path.dirname(os.path.abspath(args.output_blend)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output_blend))
        print("MG1_ARM_PREVIEWS=" + json.dumps({"blend": args.output_blend,
            "centroid_errors": {model: centroid_report[model]["holdout"] for model in ("PCA", "RBF")},
            "preview_actions": sorted(selected_preview)}))
    finally:
        scene.frame_set(previous_frame, subframe=previous_subframe)
        # The saved .blend intentionally retains only generated preview objects
        # in addition to originals; this process never saves the input file.
        # If preview generation fails before saving, clean up the temporary data.
        if not os.path.exists(args.output_blend):
            for action in list(bpy.data.actions):
                if action.name not in old_actions and action.name.startswith("Exploratory_"):
                    bpy.data.actions.remove(action, do_unlink=True)
            for obj in list(bpy.data.objects):
                if obj.name not in old_objects:
                    data = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if data and data.users == 0:
                        if obj.type == 'MESH':
                            bpy.data.meshes.remove(data)
                        elif obj.type == 'ARMATURE':
                            bpy.data.armatures.remove(data)


if __name__ == "__main__":
    main()
