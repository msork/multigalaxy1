"""Create disposable PCA holdout rigs and validate their mesh deformation."""
import argparse
import gzip
import json
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import basis_from_pose, duplicate_rig, evaluated_bone_matrices, make_action, set_frame

ARM_BONES = {f"bone_{n}" for n in (22, 23, 24, 25, 26, 27, 29, 30)}


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--preview", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    return p.parse_args(argv)


def copy_meshes(sources, rig, prefix):
    result = []
    for src in sources:
        dup = src.copy()
        dup.data = src.data.copy()
        dup.name = prefix + src.name
        dup.data.name = dup.name + "_Data"
        bpy.context.scene.collection.objects.link(dup)
        world = src.matrix_world.copy()
        dup.parent = rig
        dup.matrix_world = world
        for mod in dup.modifiers:
            if mod.type == 'ARMATURE':
                mod.object = rig
        result.append(dup)
    return result


def decode(predicted, bones, rest_bones):
    poses = {}
    for i, name in enumerate(bones):
        values = predicted[i*6:(i+1)*6]
        t = Vector((float(values[0]), float(values[1]), float(values[2])))
        r = Vector((float(values[3]), float(values[4]), float(values[5])))
        angle = r.length
        q = Quaternion(r / angle, angle) if angle > 1e-10 else Quaternion()
        delta = Matrix.LocRotScale(t, q, Vector((1, 1, 1)))
        rest = Matrix(rest_bones[name]["rest_matrix"])
        poses[name] = rest @ delta
    return poses


def arm_region_indices(obj, arm_bones=ARM_BONES):
    groups = {g.index for g in obj.vertex_groups if g.name in arm_bones}
    return [v.index for v in obj.data.vertices
            if any(a.group in groups and a.weight > 0 for a in v.groups)]


def evaluate(meshes, source_meshes, bones, arm_bones):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    positions, centroids = {}, {name: [] for name in bones}
    for src, obj in zip(source_meshes, meshes):
        idxs = arm_region_indices(src, arm_bones)
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            positions[src.name] = {i: ev.matrix_world @ mesh.vertices[i].co for i in idxs}
            group_names = {g.index: g.name for g in src.vertex_groups}
            weights = {name: {} for name in bones}
            idxset = set(idxs)
            for v in src.data.vertices:
                if v.index not in idxset:
                    continue
                for a in v.groups:
                    name = group_names.get(a.group)
                    if name in weights and a.weight > 0:
                        weights[name][v.index] = float(a.weight)
            for name, assignments in weights.items():
                total = sum(assignments.values())
                if not total:
                    continue
                point = Vector((0, 0, 0))
                for index, weight in assignments.items():
                    point += (ev.matrix_world @ mesh.vertices[index].co) * weight
                centroids[name].append(point / total)
        finally:
            ev.to_mesh_clear()
    return positions, centroids


def main():
    args = args_parse()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    predictions = np.load(args.predictions)
    target_original = bpy.data.objects[args.target_rig]
    selected = list(data["bones"])
    arm_bones = set(data.get("arm_region_bones", ARM_BONES))
    mesh_sources = [o for o in bpy.data.objects if o.type == 'MESH' and any(
        mod.type == 'ARMATURE' and mod.object == target_original for mod in o.modifiers)
        and arm_region_indices(o, arm_bones)]
    report_actions = []
    holdouts = [a for a in data["actions"] if a["split"] == "holdout"]
    for action in holdouts:
        action_name = action["action"]
        rig = duplicate_rig(target_original, "PCA_Holdout_" + action_name + "_Rig")
        meshes = copy_meshes(mesh_sources, rig, "PCA_Holdout_" + action_name + "_")
        pred = predictions[action_name]
        base_samples = []
        worst_vertex = {"error": 0.0}
        vertex_sq, vertex_count = 0.0, 0
        centroid_max = landmark_max = pose_max = 0.0
        per_sample = []
        for idx, sample in enumerate(action["samples"]):
            time = float(sample["time"])
            set_frame(bpy.context.scene, time)
            desired = decode(pred[idx], selected, data["rest_bones"])
            bases = basis_from_pose(rig, desired)
            for name, matrix in bases.items():
                rig.pose.bones[name].rotation_mode = 'QUATERNION'
                rig.pose.bones[name].matrix_basis = matrix
            bpy.context.view_layer.update()
            actual = evaluated_bone_matrices(rig)
            pose_max = max(pose_max, max(abs(float(actual[n][r][c])-float(desired[n][r][c]))
                for n in selected for r in range(4) for c in range(4)))
            positions, centroids = evaluate(meshes, mesh_sources, selected, arm_bones)
            current_vertex = 0.0
            for mesh_name, rows in positions.items():
                expected = sample["arm_region_vertex_positions_world"][mesh_name]
                for vertex, point in rows.items():
                    error = float((point - Vector(expected[str(vertex)])).length)
                    current_vertex = max(current_vertex, error)
                    vertex_sq += error * error
                    vertex_count += 1
                    if error > worst_vertex["error"]:
                        worst_vertex = {"error": error, "action": action_name,
                            "time": time, "mesh": mesh_name, "vertex": vertex}
            current_centroid = 0.0
            centroid_rows = sample["weighted_mesh_centroids"]
            for bone, points in centroids.items():
                if not points:
                    continue
                for mesh_name, rows in centroid_rows.items():
                    expected = rows[bone].get("arm_region_weighted_centroid_world")
                    if expected is not None:
                        current_centroid = max(current_centroid,
                            float((points[0] - Vector(expected)).length))
            centroid_max = max(centroid_max, current_centroid)
            current_landmark = 0.0
            for label, row in sample["branch_landmarks"].items():
                point = rig.matrix_world @ getattr(rig.pose.bones[row["bone"]], row["endpoint"])
                current_landmark = max(current_landmark,
                    float((point - Vector(row["world"])).length))
            landmark_max = max(landmark_max, current_landmark)
            per_sample.append({"time": time, "max_vertex_error_world": current_vertex,
                "max_centroid_error_world": current_centroid,
                "max_landmark_error_world": current_landmark})
            base_samples.append((time, bases))
        baked = make_action(rig, "Waluigi_ContextPCA_" + action_name, base_samples)
        report_actions.append({"action": action_name, "split": "holdout",
            "preview_rig": rig.name, "preview_action": baked.name,
            "sample_count": len(action["samples"]), "max_pose_matrix_error": pose_max,
            "max_arm_region_vertex_error_world": max(row["max_vertex_error_world"] for row in per_sample),
            "rmse_arm_region_vertex_error_world": (vertex_sq / max(1, vertex_count)) ** 0.5,
            "max_arm_region_centroid_error_world": centroid_max,
            "max_landmark_error_world": landmark_max, "worst_vertex": worst_vertex,
            "per_sample": per_sample})
    os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
    report = {"schema": "mg1.native-arm-context-pca-preview-validation.v1",
        "preview_file": os.path.abspath(args.preview), "actions": report_actions,
        "max_arm_region_vertex_error_world": max(r["max_arm_region_vertex_error_world"] for r in report_actions),
        "max_arm_region_centroid_error_world": max(r["max_arm_region_centroid_error_world"] for r in report_actions),
        "max_landmark_error_world": max(r["max_landmark_error_world"] for r in report_actions),
        "visual_gate": "numerical_pass_visual_review_required"}
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("MG1_CONTEXT_PCA_PREVIEW=" + json.dumps({key: report[key] for key in (
        "preview_file", "max_arm_region_vertex_error_world",
        "max_arm_region_centroid_error_world", "max_landmark_error_world")}))


if __name__ == "__main__":
    main()
