"""Import the raw Sluggies ANM banks and extract native Waluigi pose data.

Run in a clean Blender background process with the Sluggies IO add-on enabled.
The source .sluggie/.anm/texture files are read-only; all scene changes are
limited to the fresh process and its optional local preview/output files.
"""
import argparse
import gzip
import hashlib
import importlib
import json
import math
import os
import re
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from raw_anm_calibration import (
    WALUIGI_ARM_BONES,
    WALUIGI_ARM_TRACK_TO_BONE,
    WALUIGI_CONTEXT_BONES,
    WALUIGI_LEFT_PROFILE,
    WALUIGI_RIGHT_PROFILE,
    WALUIGI_RIGHT_HAND_TRACK_TO_BONE,
    bank_index,
    sequence_index,
    split_for_action,
    validate_profile,
)
from anatomical_semantic_frames import derive_anatomical_frames
from semantic_arm_fit_math import semantic_features

DEFAULT_ROOT = Path(r"C:\MultiGalaxy1-Assets\Working\MSS\Waluigi")
DEFAULT_MODEL = DEFAULT_ROOT / "123475520_waluigi.gpl" / "123475520_waluigi.gpl.sluggie"
DEFAULT_ANM_DIR = DEFAULT_ROOT / "anm"
DEFAULT_OUTPUT = Path("tools/blender/reports/native_waluigi_raw_anm_bilateral_v2.json.gz")
DEFAULT_VALIDATION = Path("tools/blender/reports/native_waluigi_raw_anm_bilateral_v2_validation.json")
DEFAULT_PREVIEW = Path("tools/blender/reports/raw_native_waluigi_batting_preview.blend")
DEFAULT_PRODUCTION_MATRIX = (
    43.437, 0.0, 0.0, 0.0,
    0.0, 0.0, -43.437, 0.0,
    0.0, 43.437, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)
PAIR_NAMES = (("bone_23", "bone_25"), ("bone_26", "bone_29"),
              ("bone_27", "bone_30"), ("bone_22", "bone_23"),
              ("bone_58", "bone_60"), ("bone_61", "bone_64"),
              ("bone_62", "bone_65"), ("bone_57", "bone_58"))
LANDMARKS = {
    "shoulder": ("bone_22", "head"),
    "elbow_main": ("bone_26", "head"),
    "elbow_overlap": ("bone_29", "head"),
    "wrist_main": ("bone_27", "tail"),
    "glove_wrist": ("bone_30", "head"),
    "glove_tip": ("bone_30", "tail"),
    "shoulder_right": ("bone_58", "head"),
    "elbow_main_right": ("bone_61", "head"),
    "elbow_overlap_right": ("bone_64", "head"),
    "wrist_right": ("bone_62", "tail"),
    "glove_wrist_right": ("bone_65", "head"),
    "glove_tip_right": ("bone_65", "tail"),
}


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(matrix):
    return [[float(matrix[r][c]) for c in range(4)] for r in range(4)]


def _vec(vector):
    return [float(x) for x in vector]


def _quat(quaternion):
    q = quaternion.normalized()
    return [float(q.w), float(q.x), float(q.y), float(q.z)]


def _matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c])) for r in range(4) for c in range(4))


def _rest_signature(armature):
    payload = [(b.name, b.parent.name if b.parent else None,
                tuple(round(float(v), 7) for row in b.matrix_local for v in row),
                round(float(b.length), 7)) for b in sorted(armature.bones, key=lambda x: x.name)]
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _action_bags(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                yield bag


def _action_slot(action):
    slots = {bag.slot for bag in _action_bags(action)}
    if len(slots) != 1:
        raise RuntimeError(f"{action.name}: expected one action slot/channelbag; got {len(slots)}")
    return next(iter(slots))


def _key_times(action, bone_names):
    prefixes = tuple(f'pose.bones["{name}"]' for name in bone_names)
    times = {float(point.co.x)
             for bag in _action_bags(action)
             for curve in bag.fcurves if curve.data_path.startswith(prefixes)
             for point in curve.keyframe_points}
    if not times:
        return []
    times.update(float(x) for x in action.frame_range)
    return sorted(times)


def _set_frame(scene, time):
    whole = math.floor(float(time))
    scene.frame_set(int(whole), subframe=float(time) - whole)
    bpy.context.view_layer.update()


def _bind_action(rig, action):
    rig.animation_data_create()
    rig.animation_data.action = action
    rig.animation_data.action_slot = _action_slot(action)
    rig.animation_data.action_influence = 1.0
    rig.animation_data.action_blend_type = "REPLACE"
    for pose_bone in rig.pose.bones:
        pose_bone.matrix_basis = Matrix.Identity(4)
        pose_bone.rotation_mode = "QUATERNION"
    bpy.context.view_layer.update()


def _eval_pose(rig):
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return evaluated, {pose_bone.name: pose_bone for pose_bone in evaluated.pose.bones}


def _mesh_world_vertices(mesh_obj):
    evaluated = mesh_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def _group_map(mesh_obj):
    return {group.index: group.name for group in mesh_obj.vertex_groups}


def _static_mesh_profile(meshes, rig, profile_bones, arm_bones):
    output = {}
    arm_set = set(arm_bones)
    for obj in meshes:
        group_by_index = _group_map(obj)
        per_bone = {name: [] for name in profile_bones}
        arm_region = set()
        inv_rig = rig.matrix_world.inverted_safe()
        for vertex in obj.data.vertices:
            for assignment in vertex.groups:
                name = group_by_index.get(assignment.group)
                if name not in per_bone or assignment.weight <= 0:
                    continue
                per_bone[name].append({
                    "index": int(vertex.index),
                    "weight": float(assignment.weight),
                    "position_armature_rest": _vec(inv_rig @ (obj.matrix_world @ vertex.co)),
                })
                if name in arm_set:
                    arm_region.add(int(vertex.index))
        output[obj.name] = {
            "vertex_count": len(obj.data.vertices),
            "polygon_count": len(obj.data.polygons),
            "object_matrix_world": _rows(obj.matrix_world),
            "armature_modifier_names": [m.name for m in obj.modifiers
                                         if m.type == "ARMATURE" and m.object == rig],
            "arm_region_indices": sorted(arm_region),
            "weighted_vertices_by_bone": per_bone,
        }
    return output


def _centroids(meshes, rig, profile_bones):
    result = {}
    vertices_by_mesh = {}
    inv_rig = rig.matrix_world.inverted_safe()
    for obj in meshes:
        vertices = _mesh_world_vertices(obj)
        vertices_by_mesh[obj.name] = vertices
        group_by_index = _group_map(obj)
        sums_world = {name: Vector((0.0, 0.0, 0.0)) for name in profile_bones}
        sums_armature = {name: Vector((0.0, 0.0, 0.0)) for name in profile_bones}
        totals = {name: 0.0 for name in profile_bones}
        counts = {name: 0 for name in profile_bones}
        for vertex in obj.data.vertices:
            world_pos = vertices[vertex.index]
            arm_pos = inv_rig @ world_pos
            for assignment in vertex.groups:
                name = group_by_index.get(assignment.group)
                if name not in sums_world or assignment.weight <= 0:
                    continue
                sums_world[name] += world_pos * assignment.weight
                sums_armature[name] += arm_pos * assignment.weight
                totals[name] += float(assignment.weight)
                counts[name] += 1
        result[obj.name] = {
            name: {
                "weighted_vertex_count": counts[name],
                "total_weight": totals[name],
                "weighted_centroid_world": _vec(sums_world[name] / totals[name]) if totals[name] else None,
                "weighted_centroid_armature": _vec(sums_armature[name] / totals[name]) if totals[name] else None,
            } for name in profile_bones
        }
    return result, vertices_by_mesh


def _duplicate_rig_and_meshes(rig, meshes, collection):
    duplicate = rig.copy()
    duplicate.data = rig.data.copy()
    duplicate.name = "_RawAnmIdentityReplay_Rig"
    duplicate.data.name = duplicate.name + "_Data"
    collection.objects.link(duplicate)
    duplicate.matrix_world = rig.matrix_world.copy()
    duplicate.animation_data_clear()
    for pose_bone in duplicate.pose.bones:
        pose_bone.matrix_basis = Matrix.Identity(4)
        pose_bone.rotation_mode = "QUATERNION"
    mesh_copies = []
    for source in meshes:
        copy_obj = source.copy()
        copy_obj.data = source.data.copy()
        copy_obj.name = "_RawAnmIdentityReplay_" + source.name
        copy_obj.data.name = copy_obj.name + "_Data"
        collection.objects.link(copy_obj)
        if source.parent == rig:
            copy_obj.parent = duplicate
            copy_obj.parent_type = source.parent_type
            copy_obj.parent_bone = source.parent_bone
            copy_obj.matrix_parent_inverse = source.matrix_parent_inverse.copy()
        copy_obj.matrix_world = source.matrix_world.copy()
        for modifier in copy_obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object == rig:
                modifier.object = duplicate
        mesh_copies.append(copy_obj)
    bpy.context.view_layer.update()
    return duplicate, mesh_copies


def _identity_sample(source_rig, source_pose, source_meshes, source_vertices,
                     replay_rig, replay_meshes, profile_bones):
    # Non-profile bones retain the evaluated native context. The modeled profile
    # is explicitly replayed from the extracted local matrix_basis values.
    for name, source_bone in source_pose.items():
        replay_rig.pose.bones[name].matrix_basis = source_bone.matrix_basis.copy()
    for name in profile_bones:
        replay_rig.pose.bones[name].matrix_basis = source_pose[name].matrix_basis.copy()
    bpy.context.view_layer.update()
    replay_pose = replay_rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
    pose_error = max((_matrix_error(source_pose[n].matrix, replay_pose[n].matrix)
                      for n in source_pose.keys()), default=0.0)
    vertex_error = 0.0
    for src_mesh, replay_mesh in zip(source_meshes, replay_meshes):
        replay_vertices = _mesh_world_vertices(replay_mesh)
        src_vertices = source_vertices[src_mesh.name]
        if len(src_vertices) != len(replay_vertices):
            raise RuntimeError(f"identity replay vertex count differs for {src_mesh.name}")
        vertex_error = max(vertex_error, max(((a - b).length for a, b in zip(src_vertices, replay_vertices)),
                                             default=0.0))
    return pose_error, vertex_error


def _sample_record(time, normalized_time, evaluated_rig, pose_bones,
                   armature_data, world_matrix, mesh_centroids, mesh_vertices,
                   meshes, profile_bones, arm_bones, rig):
    bones = {}
    for name in profile_bones:
        pose_bone = pose_bones[name]
        rest = armature_data.bones[name].matrix_local
        basis = pose_bone.matrix_basis
        relative = rest.inverted_safe() @ pose_bone.matrix
        loc, rot, scale = relative.decompose()
        bone_record = {
            "local_basis_matrix": _rows(basis),
            "rest_relative_se3": {
                "translation": _vec(loc),
                "rotation_quaternion_wxyz": _quat(rot),
                "scale": _vec(scale),
            },
        }
        if name in arm_bones:
            bone_record["armature_pose_matrix"] = _rows(pose_bone.matrix)
        bones[name] = bone_record
    landmarks = {}
    for key, (bone_name, endpoint) in LANDMARKS.items():
        p = getattr(pose_bones[bone_name], endpoint)
        landmarks[key] = {
            "bone": bone_name, "endpoint": endpoint,
            "armature": _vec(p), "world": _vec(world_matrix @ p),
        }
    relative_pairs = {}
    for left, right in PAIR_NAMES:
        ql = pose_bones[left].matrix.to_quaternion().normalized()
        qr = pose_bones[right].matrix.to_quaternion().normalized()
        rel = ql.inverted() @ qr
        relative_pairs[left + "__" + right] = _quat(rel)
    arm_region = {}
    arm_set = set(arm_bones)
    for obj in meshes:
        indices = set()
        groups = _group_map(obj)
        for vertex in obj.data.vertices:
            if any(groups.get(a.group) in arm_set and a.weight > 0 for a in vertex.groups):
                indices.add(vertex.index)
        coords = mesh_vertices[obj.name]
        arm_region[obj.name] = {
            "weighted_vertex_count": len(indices),
            "weighted_centroid_world": (_vec(sum((coords[i] for i in indices), Vector()) / len(indices))
                                         if indices else None),
        }
    return {
        "time": float(time), "normalized_time": float(normalized_time),
        "bones": bones, "branch_landmarks": landmarks,
        "relative_pose_quaternions_wxyz": relative_pairs,
        "weighted_mesh_centroids": mesh_centroids,
        "arm_region_mesh_summary": arm_region,
    }


def _importer_available():
    if hasattr(bpy.ops, "sluggies") and hasattr(bpy.ops.sluggies, "import_json") \
            and hasattr(bpy.ops.sluggies, "import_anm"):
        return
    try:
        bpy.ops.preferences.addon_enable(module="sluggiesio")
    except Exception as exc:
        raise RuntimeError("Enable the Sluggies IO Blender add-on v0.9.0 before extraction") from exc
    if not (hasattr(bpy.ops.sluggies, "import_json") and hasattr(bpy.ops.sluggies, "import_anm")):
        raise RuntimeError("Sluggies IO operators are unavailable; install/enable Sluggies IO v0.9.0")


def extract(args):
    model = Path(args.model).resolve()
    anm_dir = Path(args.anm_dir).resolve()
    banks = sorted(Path(p).resolve() for p in args.bank) if args.bank else sorted(anm_dir.glob("*.anm"))
    if not model.is_file() or not banks or any(not p.is_file() for p in banks):
        raise RuntimeError("model or requested ANM bank is missing")
    profile, arm_bones = validate_profile(args.bones or WALUIGI_CONTEXT_BONES,
                                           args.arm_bones or WALUIGI_ARM_BONES)
    existing_imports = [o.name for o in bpy.context.scene.objects
                        if o.type == "ARMATURE" and o.get("SluggiesChunkNumber") is not None]
    if existing_imports:
        raise RuntimeError("Run this tool in a fresh Blender scene; found existing Sluggies armatures: "
                           + ", ".join(existing_imports))
    _importer_available()
    addon_module = next((entry.module for entry in bpy.context.preferences.addons
                         if entry.module.lower().endswith("sluggiesio")), None)
    if not addon_module:
        raise RuntimeError("Sluggies IO must be enabled to parse source ANM track metadata")
    try:
        AnmFormat = importlib.import_module(addon_module + ".AnmFormat")
    except Exception as exc:
        raise RuntimeError("Sluggies IO AnmFormat parser is unavailable") from exc
    start_objects = set(bpy.data.objects)
    startup_objects = list(bpy.context.scene.objects)
    model_result = bpy.ops.sluggies.import_json(filepath=str(model), reorient_bones=True)
    if "FINISHED" not in model_result:
        raise RuntimeError("Sluggies .sluggie import failed")
    new_objects = set(bpy.data.objects) - start_objects
    rigs = [obj for obj in new_objects if obj.type == "ARMATURE"
            and obj.get("SluggiesChunkNumber") is not None]
    if len(rigs) != 1:
        raise RuntimeError("expected exactly one newly imported .sluggie armature")
    rig = rigs[0]
    imported_matrix_world = rig.matrix_world.copy()
    target_matrix = Matrix([args.target_object_matrix[i:i + 4]
                            for i in range(0, 16, 4)])
    # The add-on imports game units at object scale 1. Production Waluigi's
    # validated object basis is 43.437 with the same +90 degree X orientation.
    # This is assigned to the fresh imported duplicate; scale is never applied.
    rig.matrix_world = target_matrix
    bpy.context.view_layer.update()
    meshes = [obj for obj in new_objects if obj.type == "MESH"
              and any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers)]
    if not meshes:
        raise RuntimeError("Sluggies .sluggie import produced no armature-bound meshes")
    # Keep only the imported character visible in the diagnostic preview.
    for obj in startup_objects:
        if obj.name in {"Camera", "Cube", "Light"}:
            obj.hide_set(True)
            obj.hide_render = True
    missing = sorted(set(profile) - set(rig.data.bones.keys()))
    if missing:
        raise RuntimeError("profile bones missing from imported model: " + ", ".join(missing))
    tids = {}
    for bone in rig.data.bones:
        tid = bone.get("track_id", 0xFFFF)
        if tid != 0xFFFF:
            tids.setdefault(str(int(tid)), []).append(bone.name)
    duplicates = {tid: names for tid, names in tids.items() if len(names) != 1}
    if duplicates:
        raise RuntimeError("ambiguous duplicate TrackId mapping: " + repr(duplicates))
    track_to_bone = {tid: names[0] for tid, names in tids.items()}
    if "waluigi" in str(model).lower():
        actual_arm_tracks = {int(rig.data.bones[name].get("track_id", 0xFFFF)): name
                             for name in WALUIGI_ARM_BONES}
        if actual_arm_tracks != WALUIGI_ARM_TRACK_TO_BONE:
            raise RuntimeError("raw .sluggie arm TrackIds differ from the verified mapping: "
                               + repr(actual_arm_tracks))
        for track_id, bone_name in WALUIGI_RIGHT_HAND_TRACK_TO_BONE.items():
            actual = track_to_bone.get(str(track_id))
            if actual != bone_name:
                raise RuntimeError(f"right hand TrackId {track_id} expected {bone_name}, got {actual}")
    missing_track_bones = sorted(set(arm_bones) - set(rig.data.bones.keys()))
    if missing_track_bones:
        raise RuntimeError("armature track map lacks arm profile bones")

    rig.animation_data_create()
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bank_records = []
    for bank_path in banks:
        raw_bytes = bank_path.read_bytes()
        parsed_bank = AnmFormat.parse(raw_bytes)
        before_actions = set(bpy.data.actions)
        result = bpy.ops.sluggies.import_anm(filepath=str(bank_path), directory=str(bank_path.parent),
                                              files=[{"name": bank_path.name}])
        if "FINISHED" not in result:
            raise RuntimeError("Sluggies ANM import failed: " + bank_path.name)
        actions = [a for a in set(bpy.data.actions) - before_actions
                   if a.get("SluggiesAnmSource") and Path(a["SluggiesAnmSource"]).name == bank_path.name]
        actions.sort(key=lambda a: int(a.get("SluggiesAnmSequence", -1)))
        if not actions or any(int(a.get("SluggiesAnmSequenceCount", -1)) != len(actions) for a in actions):
            raise RuntimeError("imported sequence count/metadata mismatch for " + bank_path.name)
        if len(parsed_bank["sequences"]) != len(actions):
            raise RuntimeError("raw ANM parser and importer disagree on sequence count: " + bank_path.name)
        indices = [int(a.get("SluggiesAnmSequence", -1)) for a in actions]
        if indices != list(range(len(actions))):
            raise RuntimeError("sequence indices are missing or duplicated in " + bank_path.name)
        bank_records.append({"path": str(bank_path), "file": bank_path.name,
                             "sha256": _hash(bank_path), "size_bytes": bank_path.stat().st_size,
                             "bank_index": bank_index(bank_path.stem), "sequence_count": len(actions),
                             "actions": actions, "raw_sequences": parsed_bank["sequences"],
                             "raw_magic": int(parsed_bank["magic"]),
                             "raw_bank_id": int(parsed_bank["bank_id"])})

    rest_bones = {name: {
        "parent": rig.data.bones[name].parent.name if rig.data.bones[name].parent else None,
        "track_id": rig.data.bones[name].get("track_id", 0xFFFF),
        "rest_matrix": _rows(rig.data.bones[name].matrix_local),
        "rest_head": _vec(rig.data.bones[name].head_local),
        "rest_tail": _vec(rig.data.bones[name].tail_local),
        "rest_length": float(rig.data.bones[name].length),
    } for name in profile}
    semantic_rest = {name: {**row, "head": row["rest_head"],
                            "tail": row["rest_tail"]}
                     for name, row in rest_bones.items()}
    anatomy = derive_anatomical_frames(semantic_rest, _rows(rig.matrix_world),
                                       WALUIGI_LEFT_PROFILE,
                                       WALUIGI_RIGHT_PROFILE)
    mesh_static = _static_mesh_profile(meshes, rig, profile, arm_bones)
    replay_rig, replay_meshes = _duplicate_rig_and_meshes(rig, meshes, bpy.context.scene.collection)
    scene = bpy.context.scene
    action_rows = []
    comparison = _compare_legacy_dataset(args.compare_dataset, rig, meshes, profile, arm_bones,
                                         track_to_bone) if args.compare_dataset else None
    identity_pose_max = 0.0
    identity_vertex_max = 0.0
    identity_samples = 0
    texture_report = []

    for bank_record in bank_records:
        for action in bank_record["actions"]:
            sequence = int(action["SluggiesAnmSequence"])
            group = str(action["SluggiesAnmGroup"])
            if group != Path(bank_record["file"]).stem:
                raise RuntimeError("action group metadata differs from source bank")
            slot = _action_slot(action)
            _bind_action(rig, action)
            raw_sequence = bank_record["raw_sequences"][sequence]
            raw_key_times = sorted({float(key["time"]) for track in raw_sequence for key in track["keys"]})
            key_times = sorted(set(_key_times(action, profile)) | set(raw_key_times))
            if not key_times:
                raise RuntimeError(action.name + " has no keyed profile channels")
            lo, hi = map(float, action.frame_range)
            duration = max(hi - lo, 0.0)
            samples = []
            for frame_time in key_times:
                _set_frame(scene, frame_time)
                evaluated_rig, pose_bones = _eval_pose(rig)
                mesh_centroids, mesh_vertices = _centroids(meshes, rig, profile)
                record = _sample_record(frame_time, (frame_time - lo) / duration if duration else 0.0,
                                        evaluated_rig, pose_bones, rig.data, evaluated_rig.matrix_world,
                                        mesh_centroids, mesh_vertices, meshes, profile, arm_bones, rig)
                sample_pose = {name: _rows(pose_bones[name].matrix) for name in profile}
                record["anatomical_semantics"] = {
                    "left": semantic_features(semantic_rest, sample_pose,
                        WALUIGI_LEFT_PROFILE, anatomy["torso_to_anatomical"],
                        anatomy["sides"]["left"]["forearm_to_anatomical"],
                        anatomy["sides"]["left"]["forearm_axes_local"],
                        elbow_absolute=True).tolist(),
                    "right": semantic_features(semantic_rest, sample_pose,
                        WALUIGI_RIGHT_PROFILE, anatomy["torso_to_anatomical"],
                        anatomy["sides"]["right"]["forearm_to_anatomical"],
                        anatomy["sides"]["right"]["forearm_axes_local"],
                        elbow_absolute=True).tolist(),
                }
                # The duplicate receives exactly the extracted profile local
                # bases while all other bones preserve this raw action sample.
                for name, pose_bone in pose_bones.items():
                    replay_rig.pose.bones[name].matrix_basis = pose_bone.matrix_basis.copy()
                for name in profile:
                    replay_rig.pose.bones[name].matrix_basis = Matrix(record["bones"][name]["local_basis_matrix"])
                bpy.context.view_layer.update()
                replay_eval = replay_rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
                replay_pose = replay_eval.pose.bones
                pose_err = max((_matrix_error(pose_bones[n].matrix, replay_pose[n].matrix)
                                for n in pose_bones), default=0.0)
                vertex_err = 0.0
                mesh_errors = {}
                for source_mesh, replay_mesh in zip(meshes, replay_meshes):
                    actual = _mesh_world_vertices(replay_mesh)
                    reference = mesh_vertices[source_mesh.name]
                    errors = [float((a - b).length) for a, b in zip(reference, actual)]
                    mesh_errors[source_mesh.name] = max(errors, default=0.0)
                    vertex_err = max(vertex_err, mesh_errors[source_mesh.name])
                identity_pose_max = max(identity_pose_max, pose_err)
                identity_vertex_max = max(identity_vertex_max, vertex_err)
                identity_samples += 1
                record["identity_replay"] = {"max_pose_matrix_error": pose_err,
                                              "max_mesh_vertex_error_world": vertex_err,
                                              "mesh_vertex_error_world": mesh_errors}
                samples.append(record)
            source_track_ids = sorted({int(track["track_id"]) for track in raw_sequence})
            source_track_records = [{
                "track_id": int(track["track_id"]),
                "length": float(track["length"]),
                "type": int(track["type"]),
                "interpolation": int(track["interp"]),
                "replace_hierarchy": int(track["replace"]),
                "key_count": len(track["keys"]),
            } for track in raw_sequence]
            mapped_track_bones = {str(tid): track_to_bone[str(tid)] for tid in source_track_ids
                                  if str(tid) in track_to_bone}
            action_rows.append({
                "action": action.name,
                "source_action": action.name,
                "bank": group,
                "bank_index": bank_record["bank_index"],
                "sequence_index": sequence,
                "sequence_count": int(action["SluggiesAnmSequenceCount"]),
                "source_path": str(bank_record["path"]),
                "source_sha256": bank_record["sha256"],
                "source_magic": int(action["SluggiesAnmMagic"]),
                "source_bank_id": int(action["SluggiesAnmBankId"]),
                "action_slot": slot.identifier,
                "frame_range": [lo, hi],
                "duration": duration,
                "exact_key_times": key_times,
                "split": split_for_action(bank_record["bank_index"], sequence),
                "source_track_ids": source_track_ids,
                "source_track_to_bone": mapped_track_bones,
                "source_track_records": source_track_records,
                "raw_anm_key_times": raw_key_times,
                "samples": samples,
            })
            if args.max_actions and len(action_rows) >= args.max_actions:
                break
        if args.max_actions and len(action_rows) >= args.max_actions:
            break
    # Broken/absent image paths are an error for a preview; source assets are
    # never altered. A generated dataset can still be written without preview.
    for image in bpy.data.images:
        if image.users == 0:
            continue
        path = bpy.path.abspath(image.filepath) if image.source == "FILE" else ""
        exists = bool(path and os.path.isfile(path))
        if image.source == "FILE":
            texture_report.append({"name": image.name, "path": path, "exists": exists,
                                   "size": list(image.size), "packed": bool(image.packed_file),
                                   "users": int(image.users)})
            if args.preview and (image.size[0] == 0 or image.size[1] == 0 or (not exists and not image.packed_file)):
                raise RuntimeError("preview has a missing/invalid source texture: " + image.name)
    # Identity-only copies must never be visible in the saved native preview.
    for obj in [*replay_meshes, replay_rig]:
        bpy.data.objects.remove(obj, do_unlink=True)

    if args.preview:
        preview_action = bpy.data.actions.get(args.preview_action)
        if preview_action is None:
            preview_action = next((a for a in bpy.data.actions if a.get("SluggiesAnmSource")
                                   and int(a.get("SluggiesAnmSequence", -1)) == 10
                                   and a.get("SluggiesAnmGroup", "").startswith("Waluigi_7_")), None)
        if preview_action is None:
            preview_action = bank_records[0]["actions"][0]
        _bind_action(rig, preview_action)
        _set_frame(scene, args.preview_frame)
        for obj in [rig, *meshes]:
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_set(False)
        # Keep the preview self-contained while preserving the real source maps.
        bpy.ops.file.pack_all()
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.preview.resolve()))

    left_track_ids = set(WALUIGI_LEFT_ARM_TRACK_TO_BONE)
    right_track_ids = set(WALUIGI_RIGHT_ARM_TRACK_TO_BONE)
    def side_sampling(track_ids):
        actions_with_tracks = [row for row in action_rows
            if track_ids.intersection(row["source_track_ids"])]
        return {"pose_samples": sum(len(row["samples"]) for row in action_rows),
                "actions_with_arm_tracks": len(actions_with_tracks),
                "samples_in_actions_with_arm_tracks": sum(
                    len(row["samples"]) for row in actions_with_tracks)}

    report = {
        "schema": "mg1.raw-sluggies-native-calibration.v2",
        "read_only_sources": True,
        "importer": "Sluggies IO Blender add-on v0.9.0 operators; reorient_bones=true",
        "model": {"path": str(model), "sha256": _hash(model), "size_bytes": model.stat().st_size},
        "rig": {"object": rig.name, "data": rig.data.name, "bone_count": len(rig.data.bones),
                "rest_signature_sha256": _rest_signature(rig.data),
                "importer_matrix_world": _rows(imported_matrix_world),
                "production_basis_matrix_world": _rows(rig.matrix_world),
                "matrix_world": _rows(rig.matrix_world), "scale": _vec(rig.scale),
                "track_id_to_bone": track_to_bone, "profile_bones": list(profile),
                "arm_region_bones": list(arm_bones), "rest_bones": rest_bones,
                "semantic_profiles": {"left": WALUIGI_LEFT_PROFILE,
                                      "right": WALUIGI_RIGHT_PROFILE},
                "hand_position_sign_convention": "positive X is character-right in the shared anatomical frame; outward motion is negative on the character-left and positive on the character-right"},
        "meshes": mesh_static,
        "banks": [{k: v for k, v in record.items() if k not in ("actions", "raw_sequences")}
                  for record in bank_records],
        "provenance": {
            "authority": "raw native .anm bank imported onto matching .sluggie skeleton by TrackId",
            "arm_track_ids_verified": {name: int(rig.data.bones[name]["track_id"]) for name in arm_bones},
            "right_hand_track_ids_verified": {name: int(rig.data.bones[name]["track_id"])
                for name in WALUIGI_RIGHT_HAND_TRACK_TO_BONE.values()},
            "side_extraction_policy": "left and right pose transforms are sampled directly from raw ANM channels by their own TrackIds; no mirrored right samples",
            "candidate_owner_conversion": "not applied; imported .sluggie rest basis equals production Waluigi rest basis",
            "legacy_action_mapping": comparison,
        },
        "sample_time_policy": "union of exact imported action key/subframe times on context bones plus action range endpoints",
        "identity_replay": {"sample_count": identity_samples,
                            "max_pose_matrix_error": identity_pose_max,
                            "max_mesh_vertex_error_world": identity_vertex_max},
        "texture_inventory": texture_report,
        "holdout_policy": "whole action; Bank 7 keeps legacy sequence holdouts 11,15,20,24,26; other banks use (sequence+bank)%5==0",
        "training_action_count": sum(row["split"] == "train" for row in action_rows),
        "holdout_action_count": sum(row["split"] == "holdout" for row in action_rows),
        "sample_count": sum(len(row["samples"]) for row in action_rows),
        "side_sampling": {"left": side_sampling(left_track_ids),
                          "right": side_sampling(right_track_ids)},
        "actions": action_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8", compresslevel=6) as target:
        json.dump(report, target, separators=(",", ":"), allow_nan=False)
    compact = {
        "schema": report["schema"],
        "model_sha256": report["model"]["sha256"],
        "rest_signature_sha256": report["rig"]["rest_signature_sha256"],
        "bone_count": report["rig"]["bone_count"],
        "bank_count": len(bank_records),
        "banks": [{k: v for k, v in record.items() if k not in ("actions", "raw_sequences")}
                  for record in bank_records],
        "action_count": len(action_rows),
        "training_action_count": report["training_action_count"],
        "holdout_action_count": report["holdout_action_count"],
        "sample_count": report["sample_count"],
        "side_sampling": report["side_sampling"],
        "identity_replay": report["identity_replay"],
        "legacy_action_mapping": comparison,
        "texture_inventory": texture_report,
        "preview": str(args.preview.resolve()) if args.preview else None,
        "dataset": str(args.output.resolve()),
    }
    args.validation_output.parent.mkdir(parents=True, exist_ok=True)
    args.validation_output.write_text(json.dumps(compact, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("RAW_WALUIGI_CALIBRATION=" + json.dumps({
        "dataset": compact["dataset"], "preview": compact["preview"],
        "actions": compact["action_count"], "samples": compact["sample_count"],
        "identity": compact["identity_replay"],
        "legacy_mapping_max_pose_error": (comparison or {}).get("max_pose_matrix_error"),
    }, separators=(",", ":")))


def _compare_legacy_dataset(path, raw_rig, meshes, profile, arm_bones, track_to_bone):
    import gzip
    if not path.is_file():
        raise RuntimeError("legacy comparison dataset is missing: " + str(path))
    with gzip.open(path, "rt", encoding="utf-8") as source:
        legacy = json.load(source)
    old_actions = legacy.get("actions", [])
    rows = []
    scene = bpy.context.scene
    new_actions = [a for a in bpy.data.actions if a.get("SluggiesAnmSource")]
    raw_by_index = {(int(a.get("SluggiesAnmBankId", -1)), int(a.get("SluggiesAnmSequence", -1)),
                     Path(a.get("SluggiesAnmSource", "")).stem): a for a in new_actions}
    legacy_holdouts = {"sequence_11", "sequence_15", "sequence_20", "sequence_24", "sequence_26"}
    all_matrix_errors, all_arm_errors = [], []
    for old in old_actions:
        match = re.match(r"^sequence_(\d+)", old.get("action", ""))
        if not match:
            continue
        index = int(match.group(1))
        raw_action = next((a for a in new_actions
                           if a.get("SluggiesAnmGroup") == "Waluigi_7_Batting"
                           and int(a.get("SluggiesAnmSequence", -1)) == index), None)
        if raw_action is None:
            rows.append({"legacy_action": old.get("action"), "error": "raw Bank 7 sequence missing"})
            continue
        ratio = float(raw_action.frame_range[1] - raw_action.frame_range[0]) / max(
            float(old["frame_range"][1] - old["frame_range"][0]), 1e-9)
        _bind_action(raw_rig, raw_action)
        per_bone = {name: 0.0 for name in profile}
        mesh_vertex_error = 0.0
        centroid_errors = []
        for old_sample in old.get("samples", []):
            source_time = float(old_sample["time"])
            raw_time = float(raw_action.frame_range[0]) + (source_time - float(old["frame_range"][0])) * ratio
            _set_frame(scene, raw_time)
            evaluated, pose_bones = _eval_pose(raw_rig)
            for name in profile:
                legacy_matrix = old_sample.get("bones", {}).get(name, {}).get("armature_pose")
                if legacy_matrix is None:
                    continue
                error = _matrix_error(pose_bones[name].matrix, Matrix(legacy_matrix))
                per_bone[name] = max(per_bone[name], error)
                all_matrix_errors.append(error)
                if name in arm_bones:
                    all_arm_errors.append(error)
            raw_centroids, raw_vertices = _centroids(meshes, raw_rig, profile)
            for raw_mesh_name, bones in raw_centroids.items():
                old_mesh_name = next((n for n in old_sample.get("weighted_mesh_centroids", {})
                                      if n.endswith("submesh0") == raw_mesh_name.endswith("submesh0")
                                      and n.endswith("submesh1") == raw_mesh_name.endswith("submesh1")), None)
                if old_mesh_name is None:
                    continue
                for bone_name in arm_bones:
                    a = old_sample["weighted_mesh_centroids"].get(old_mesh_name, {}).get(bone_name, {}).get("weighted_centroid_world")
                    b = bones.get(bone_name, {}).get("weighted_centroid_world")
                    if a is not None and b is not None:
                        centroid_errors.append((Vector(a) - Vector(b)).length)
                old_region = old_sample.get("arm_region_vertex_positions_world", {}).get(old_mesh_name, {})
                for index_str, position in old_region.items():
                    idx = int(index_str)
                    if idx < len(raw_vertices[raw_mesh_name]):
                        mesh_vertex_error = max(mesh_vertex_error,
                                                (Vector(position) - raw_vertices[raw_mesh_name][idx]).length)
        rows.append({
            "legacy_action": old.get("action"), "raw_action": raw_action.name,
            "sequence_index": index, "duration_ratio": ratio,
            "samples_compared": len(old.get("samples", [])),
            "max_profile_pose_matrix_error": max(per_bone.values(), default=0.0),
            "max_arm_pose_matrix_error": max((per_bone[n] for n in arm_bones), default=0.0),
            "max_weighted_centroid_error_world": max(centroid_errors, default=0.0),
            "max_arm_region_vertex_error_world": mesh_vertex_error,
            "worst_profile_bones": sorted(per_bone.items(), key=lambda x: x[1], reverse=True)[:6],
            "split_preserved": ("holdout" if re.match(r"^sequence_(\d+)", old.get("action", ""))
                                and int(index) in {11, 15, 20, 24, 26} else "train"),
        })
    return {
        "source_dataset": str(path),
        "mapping": "legacy sequence index i -> Waluigi_7_Batting_seq<i>; raw time = legacy time * action duration ratio (expected 6)",
        "matched_action_count": sum("raw_action" in row for row in rows),
        "unmatched_action_count": sum("error" in row for row in rows),
        "max_pose_matrix_error": max(all_matrix_errors, default=0.0),
        "max_arm_pose_matrix_error": max(all_arm_errors, default=0.0),
        "action_results": rows,
    }


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--anm-dir", type=Path, default=DEFAULT_ANM_DIR)
    parser.add_argument("--bank", action="append", default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation-output", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--compare-dataset", type=Path,
                        default=Path("tools/blender/reports/native_waluigi_arm_context_production_basis.json.gz"))
    parser.add_argument("--no-compare-dataset", action="store_true",
                        help="skip legacy inferred-dataset comparison")
    parser.add_argument("--preview", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument("--no-preview", action="store_true",
                        help="write dataset/report without saving a Blender preview")
    parser.add_argument("--preview-action", default="Waluigi_7_Batting_seq10")
    parser.add_argument("--preview-frame", type=float, default=41.0)
    parser.add_argument("--target-object-matrix", nargs=16, type=float,
                        default=DEFAULT_PRODUCTION_MATRIX,
                        help="validated 4x4 target object matrix, row-major; does not apply scale")
    parser.add_argument("--max-actions", type=int, default=0, help="debug cap; zero imports all sequences")
    parser.add_argument("--bone", dest="bones", action="append")
    parser.add_argument("--arm-bone", dest="arm_bones", action="append")
    args = parser.parse_args(argv)
    if args.no_compare_dataset:
        args.compare_dataset = None
    if args.no_preview:
        args.preview = None
    extract(args)


if __name__ == "__main__":
    main()
