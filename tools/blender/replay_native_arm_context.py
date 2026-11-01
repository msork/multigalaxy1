"""No-compression identity replay of production-basis context samples.

The reference is the full converted native pose and mesh capture stored in the
dataset. Only a duplicate target rig and duplicate bound meshes are mutated.
"""
import argparse
import gzip
import json
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import basis_from_pose, duplicate_rig, evaluated_bone_matrices, set_frame

ARM_BONES = {f"bone_{n}" for n in (22, 23, 24, 25, 26, 27, 29, 30)}


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    return parser.parse_args(argv)


def copy_meshes(sources, rig):
    copies = []
    for src in sources:
        dup = src.copy()
        dup.data = src.data.copy()
        dup.name = "_MG1_ContextReplay_" + src.name
        dup.data.name = dup.name + "_Data"
        bpy.context.scene.collection.objects.link(dup)
        world = src.matrix_world.copy()
        dup.parent = rig
        dup.matrix_world = world
        for mod in dup.modifiers:
            if mod.type == 'ARMATURE':
                mod.object = rig
        copies.append(dup)
    return copies


def region_indices(obj, arm_bones=ARM_BONES):
    groups = {g.index for g in obj.vertex_groups if g.name in arm_bones}
    return [v.index for v in obj.data.vertices
            if any(a.group in groups and a.weight > 0 for a in v.groups)]


def reset(rig):
    rig.animation_data_clear()
    for pb in rig.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.matrix_basis = Matrix.Identity(4)


def matrix_err(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c])) for r in range(4) for c in range(4))


def evaluate_region(meshes, sources, bones, arm_bones):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = {}
    for src, obj in zip(sources, meshes):
        indices = region_indices(src, arm_bones)
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            result[src.name] = {i: ev.matrix_world @ mesh.vertices[i].co for i in indices}
        finally:
            ev.to_mesh_clear()
    centroids = {src.name: {name: None for name in bones} for src in sources}
    for src, obj in zip(sources, meshes):
        groups = {g.index: g.name for g in src.vertex_groups}
        indices = set(region_indices(src, arm_bones))
        weights = {name: {} for name in bones}
        for vertex in src.data.vertices:
            if vertex.index not in indices:
                continue
            for assignment in vertex.groups:
                name = groups.get(assignment.group)
                if name in weights and assignment.weight > 0:
                    weights[name][vertex.index] = float(assignment.weight)
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            for name, assignments in weights.items():
                total = sum(assignments.values())
                if not total:
                    continue
                point = Vector((0, 0, 0))
                for index, weight in assignments.items():
                    point += (ev.matrix_world @ mesh.vertices[index].co) * weight
                    centroids[src.name][name] = point / total
        finally:
            ev.to_mesh_clear()
    return result, centroids


def main():
    args = args_parse()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    target_original = bpy.data.objects[args.target_rig]
    selected = list(data["bones"])
    arm_bones = set(data.get("arm_region_bones", ARM_BONES))
    sources = [obj for obj in bpy.data.objects if obj.type == 'MESH' and any(
        mod.type == 'ARMATURE' and mod.object == target_original for mod in obj.modifiers)]
    sources = [obj for obj in sources if region_indices(obj, arm_bones)]
    old_frame, old_subframe = bpy.context.scene.frame_current, bpy.context.scene.frame_subframe
    old_objects = {obj.name for obj in bpy.data.objects}
    report_actions = []
    try:
        rig = duplicate_rig(target_original, "_MG1_ContextReplay_Rig")
        meshes = copy_meshes(sources, rig)
        for action in data["actions"]:
            reset(rig)
            matrix_max = vertex_max = centroid_max = landmark_max = 0.0
            vertex_sq = 0.0
            vertex_count = 0
            worst = None
            for sample in action["samples"]:
                set_frame(bpy.context.scene, float(sample["time"]))
                desired = {name: Matrix(sample["bones"][name]["armature_pose"]) for name in selected}
                for name, basis in basis_from_pose(rig, desired).items():
                    rig.pose.bones[name].rotation_mode = 'QUATERNION'
                    rig.pose.bones[name].matrix_basis = basis
                bpy.context.view_layer.update()
                actual = evaluated_bone_matrices(rig)
                matrix_max = max(matrix_max, max(matrix_err(actual[name], desired[name]) for name in selected))
                vertex_positions, centroids = evaluate_region(meshes, sources, selected, arm_bones)
                for mesh_name, positions in vertex_positions.items():
                    expected = sample["arm_region_vertex_positions_world"][mesh_name]
                    for index, point in positions.items():
                        err = float((point - Vector(expected[str(index)])).length)
                        vertex_sq += err * err
                        vertex_count += 1
                        if err > vertex_max:
                            vertex_max = err
                            worst = {"time": float(sample["time"]), "mesh": mesh_name,
                                     "vertex": index, "error_world": err}
                for mesh_name, by_bone in centroids.items():
                    expected_mesh_rows = sample["weighted_mesh_centroids"].get(mesh_name, {})
                    for name, point in by_bone.items():
                        if point is None:
                            continue
                        expected = expected_mesh_rows.get(name, {}).get(
                            "arm_region_weighted_centroid_world")
                        if expected is not None:
                            centroid_max = max(centroid_max,
                                float((point - Vector(expected)).length))
                for label, row in sample["branch_landmarks"].items():
                    point = rig.matrix_world @ getattr(rig.pose.bones[row["bone"]], row["endpoint"])
                    err = float((point - Vector(row["world"])).length)
                    landmark_max = max(landmark_max, err)
            report_actions.append({"action": action["action"], "split": action["split"],
                "sample_count": len(action["samples"]), "max_pose_matrix_error": matrix_max,
                "max_arm_region_vertex_error_world": vertex_max,
                "rmse_arm_region_vertex_error_world": (vertex_sq / max(1, vertex_count)) ** 0.5,
                "max_arm_region_centroid_error_world": centroid_max,
                "max_landmark_error_world": landmark_max, "worst_vertex": worst})
        report = {"schema": "mg1.native-arm-context-identity-replay.v1",
            "dataset": args.dataset, "selected_bones": selected,
            "arm_region_bones": sorted(arm_bones),
            "actions": report_actions,
            "max_pose_matrix_error": max(row["max_pose_matrix_error"] for row in report_actions),
            "max_arm_region_vertex_error_world": max(row["max_arm_region_vertex_error_world"] for row in report_actions),
            "max_arm_region_vertex_rmse_world": max(row["rmse_arm_region_vertex_error_world"] for row in report_actions),
            "max_arm_region_centroid_error_world": max(row["max_arm_region_centroid_error_world"] for row in report_actions),
            "max_landmark_error_world": max(row["max_landmark_error_world"] for row in report_actions)}
        report["identity_gate"] = "pass" if report["max_pose_matrix_error"] < 1e-4 and \
            report["max_arm_region_vertex_error_world"] < 1e-3 and \
            report["max_arm_region_centroid_error_world"] < 1e-3 and \
            report["max_landmark_error_world"] < 1e-3 else "fail"
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print("MG1_CONTEXT_IDENTITY=" + json.dumps({key: report[key] for key in (
            "identity_gate", "max_pose_matrix_error", "max_arm_region_vertex_error_world",
            "max_arm_region_centroid_error_world", "max_landmark_error_world")}))
    finally:
        bpy.context.scene.frame_set(old_frame, subframe=old_subframe)
        for obj in list(bpy.data.objects):
            if obj.name not in old_objects:
                if obj.type == 'MESH' and obj.data:
                    mesh_data = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if mesh_data.users == 0:
                        bpy.data.meshes.remove(mesh_data)
                elif obj.type == 'ARMATURE' and obj.data:
                    armature_data = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if armature_data.users == 0:
                        bpy.data.armatures.remove(armature_data)


if __name__ == "__main__":
    main()
