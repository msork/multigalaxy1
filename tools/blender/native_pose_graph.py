"""Discrete native-pose graph construction and layered path solver.

The graph contains no free pose coordinates: states reference exact native
samples, and path transitions are constrained by temporal or bounded-neighbor
edges.
"""
from __future__ import annotations

import math

import numpy as np


def pose_pair_distance(a, b):
    """Return per-bone sign-invariant normalized SE(3) distances.

    A descriptor row stores normalized translation xyz followed by quaternion
    wxyz. Translation normalization and quaternion angular distance match the
    walk continuity gate.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 7:
        raise ValueError("pose descriptors must have matching bone x 7 shapes")
    qa = a[:, 3:]
    qb = b[:, 3:]
    qa = qa / np.maximum(np.linalg.norm(qa, axis=1, keepdims=True), 1e-12)
    qb = qb / np.maximum(np.linalg.norm(qb, axis=1, keepdims=True), 1e-12)
    dot = np.clip(np.abs(np.sum(qa * qb, axis=1)), 0.0, 1.0)
    angle = 2.0 * np.arccos(dot)
    translation = np.linalg.norm(b[:, :3] - a[:, :3], axis=1)
    per_bone = np.sqrt(angle * angle + translation * translation)
    return float(np.max(per_bone)), float(np.sqrt(np.mean(per_bone * per_bone))), per_bone


def build_native_pose_graph(semantic_features, pose_descriptors,
                            action_groups, neighbor_count=12,
                            semantic_radius=1.5,
                            pose_jump_limit=0.5):
    """Build directed temporal links and symmetric bounded semantic neighbors.

    `action_groups` maps action names to node ids in native sample order.
    Semantic neighbor discovery is chunked, avoiding a full N x N allocation.
    """
    semantics = np.asarray(semantic_features, dtype=np.float64)
    poses = np.asarray(pose_descriptors, dtype=np.float64)
    if semantics.ndim != 2 or poses.ndim != 3 or poses.shape[0] != len(semantics):
        raise ValueError("graph semantic and pose sample counts differ")
    if poses.shape[2] != 7:
        raise ValueError("pose descriptors must end in 7 translation/quaternion values")
    count = len(semantics)
    temporal_next = {}
    for ids in action_groups.values():
        for left, right in zip(ids, ids[1:]):
            temporal_next[int(left)] = int(right)

    neighbors = [set() for _ in range(count)]
    block_size = 96
    for start in range(0, count, block_size):
        stop = min(count, start + block_size)
        delta = semantics[start:stop, None, :] - semantics[None, :, :]
        distance = np.linalg.norm(delta, axis=2)
        for local, node in enumerate(range(start, stop)):
            row = distance[local]
            row[node] = np.inf
            near = np.argpartition(row, min(neighbor_count, count - 1) - 1)[:neighbor_count]
            near = near[np.argsort(row[near])]
            for other in near:
                if row[other] > semantic_radius:
                    continue
                maximum, _rms, _per_bone = pose_pair_distance(poses[node], poses[other])
                if maximum >= pose_jump_limit:
                    continue
                neighbors[node].add(int(other))
                neighbors[int(other)].add(node)
    return {"temporal_next": temporal_next,
            "semantic_neighbors": [tuple(sorted(row)) for row in neighbors],
            "node_count": count,
            "temporal_edge_count": len(temporal_next),
            "neighbor_edge_count": sum(map(len, neighbors)) // 2}


def solve_native_pose_path(candidate_ids, state_costs, state_semantics,
                           state_pose_descriptors, source_semantics,
                           node_actions, node_normalized_times, graph,
                           max_pose_jump=0.5, semantic_velocity_weight=1.0,
                           pose_transition_weight=8.0,
                           neighbor_switch_cost=4.0, hold_cost=12.0,
                           action_switch_cost=24.0,
                           timing_weight=0.5):
    """Find the minimum-cost path through one candidate layer per source frame."""
    frame_count = len(candidate_ids)
    if frame_count == 0 or len(state_costs) != frame_count:
        raise ValueError("path graph requires matching non-empty state layers")
    source = np.asarray(source_semantics, dtype=np.float64)
    if source.shape[0] != frame_count:
        raise ValueError("source semantic trajectory has wrong frame count")
    costs = np.asarray(state_costs[0], dtype=np.float64).copy()
    if costs.shape != (len(candidate_ids[0]),):
        raise ValueError("first state-cost layer has wrong size")
    backpointers = []
    transition_rows = []
    temporal = graph["temporal_next"]
    neighbors = graph["semantic_neighbors"]

    for frame in range(1, frame_count):
        previous_ids = candidate_ids[frame - 1]
        current_ids = candidate_ids[frame]
        previous_sem = np.asarray(state_semantics[frame - 1], dtype=np.float64)
        current_sem = np.asarray(state_semantics[frame], dtype=np.float64)
        previous_pose = np.asarray(state_pose_descriptors[frame - 1], dtype=np.float64)
        current_pose = np.asarray(state_pose_descriptors[frame], dtype=np.float64)
        layer_costs = np.full(len(current_ids), np.inf, dtype=np.float64)
        layer_back = np.full(len(current_ids), -1, dtype=np.int64)
        layer_edges = [[None] * len(current_ids) for _ in previous_ids]
        source_velocity = source[frame] - source[frame - 1]
        for j, node_j in enumerate(current_ids):
            for i, node_i in enumerate(previous_ids):
                edge_kind = None
                if node_i == node_j:
                    edge_kind = "hold"
                elif temporal.get(int(node_i)) == int(node_j):
                    edge_kind = "temporal"
                elif int(node_j) in neighbors[int(node_i)]:
                    edge_kind = "neighbor"
                if edge_kind is None or not math.isfinite(costs[i]):
                    continue
                same_action = node_actions[int(node_i)] == node_actions[int(node_j)]
                if (same_action and
                        float(node_normalized_times[int(node_j)]) + 1e-12 <
                        float(node_normalized_times[int(node_i)])):
                    # Native-neighbor links may skip forward, but must not
                    # reverse time inside a clip. This prevents a path from
                    # ping-ponging between nearly identical neighboring poses.
                    continue
                maximum, rms, _per_bone = pose_pair_distance(
                    previous_pose[i], current_pose[j])
                if maximum >= max_pose_jump:
                    continue
                semantic_velocity = current_sem[j] - previous_sem[i]
                velocity_error = float(np.sum((semantic_velocity - source_velocity) ** 2))
                phase_delta = (float(node_normalized_times[int(node_j)]) -
                               float(node_normalized_times[int(node_i)]))
                phase_error = (phase_delta - 1.0 / max(frame_count - 1, 1)) ** 2
                edge_penalty = (neighbor_switch_cost if edge_kind == "neighbor" else
                                hold_cost if edge_kind == "hold" else 0.0)
                switched_action = not same_action
                if switched_action:
                    edge_penalty += action_switch_cost
                value = (costs[i] + float(state_costs[frame][j]) +
                         pose_transition_weight * (rms * rms + maximum * maximum) +
                         semantic_velocity_weight * velocity_error +
                         timing_weight * phase_error + edge_penalty)
                layer_edges[i][j] = {"kind": edge_kind, "maximum_jump": maximum,
                                     "rms_jump": rms, "velocity_error": velocity_error,
                                     "phase_error": phase_error,
                                     "action_switch": bool(switched_action)}
                if value < layer_costs[j]:
                    layer_costs[j] = value
                    layer_back[j] = i
        if not np.isfinite(layer_costs).any():
            raise RuntimeError(f"no admissible native-pose path reaches source frame {frame}")
        backpointers.append(layer_back)
        transition_rows.append(layer_edges)
        costs = layer_costs

    state_index = int(np.argmin(costs))
    path_indices = [state_index]
    for layer_back in reversed(backpointers):
        state_index = int(layer_back[state_index])
        if state_index < 0:
            raise RuntimeError("native-pose path backtracking encountered a missing predecessor")
        path_indices.append(state_index)
    path_indices.reverse()
    path_nodes = [int(candidate_ids[frame][state])
                  for frame, state in enumerate(path_indices)]
    transitions = []
    for frame in range(1, frame_count):
        i, j = path_indices[frame - 1], path_indices[frame]
        transitions.append(transition_rows[frame - 1][i][j])
    return {"node_ids": path_nodes,
            "layer_indices": path_indices,
            "total_cost": float(costs.min()),
            "state_cost": float(sum(float(state_costs[t][idx])
                                     for t, idx in enumerate(path_indices))),
            "transitions": transitions,
            "temporal_transition_count": sum(row["kind"] == "temporal" for row in transitions),
            "neighbor_transition_count": sum(row["kind"] == "neighbor" for row in transitions),
            "hold_transition_count": sum(row["kind"] == "hold" for row in transitions),
            "action_switch_count": sum(row["action_switch"] for row in transitions),
            "maximum_transition_jump": max((row["maximum_jump"] for row in transitions), default=0.0)}
