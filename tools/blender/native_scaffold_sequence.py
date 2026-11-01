"""Monotonic dynamic-program selection within one native action scaffold."""
from __future__ import annotations

import math

import numpy as np

from native_pose_graph import pose_pair_distance


def solve_scaffold_sequence(source_semantics, scaffold_semantics,
                            scaffold_pose_descriptors, phase_radius=6,
                            phase_weight=0.08, semantic_weight=1.0,
                            pose_transition_weight=8.0,
                            semantic_velocity_weight=0.25,
                            phase_velocity_weight=0.5,
                            phase_acceleration_weight=0.3,
                            max_pose_jump=10.0, max_step=2):
    """Fit one semantic sequence to nearby samples from a single native clip.

    The second-order DP state retains the previous phase step, so phase velocity
    changes are penalized. Candidate transitions can hold or advance one/two
    native samples; they may never reverse or cross clips. Full-pose transition
    checks are sign-invariant through :func:`pose_pair_distance`. The fit path
    is not truncated by the final 0.5 acceptance threshold; that threshold is
    checked against the composed/baked candidate afterward so failures remain
    diagnosable instead of aborting the entire report.
    """
    source = np.asarray(source_semantics, dtype=np.float64)
    target = np.asarray(scaffold_semantics, dtype=np.float64)
    poses = np.asarray(scaffold_pose_descriptors, dtype=np.float64)
    if source.ndim != 2 or target.ndim != 2 or source.shape[1] != target.shape[1]:
        raise ValueError("source and scaffold semantic arrays must share feature width")
    if poses.ndim != 3 or len(poses) != len(target) or poses.shape[2] != 7:
        raise ValueError("pose descriptors must be sample x bone x 7")
    frames, samples = len(source), len(target)
    if frames == 0 or samples == 0:
        raise ValueError("scaffold solve needs source frames and target samples")
    source_std = np.maximum(np.std(np.vstack((source, target)), axis=0), 1e-4)
    src = source / source_std
    dst = target / source_std
    candidates = []
    for frame in range(frames):
        expected = frame * (samples - 1) / max(frames - 1, 1)
        radius = phase_radius * (samples - 1) / max(frames - 1, 1)
        candidates.append(np.arange(max(0, int(math.floor(expected - radius))),
                                    min(samples, int(math.ceil(expected + radius)) + 1),
                                    dtype=int))
    state_cost = []
    state_sem = []
    for frame, ids in enumerate(candidates):
        semantic_error = np.sum((dst[ids] - src[frame]) ** 2, axis=1)
        expected = frame * (samples - 1) / max(frames - 1, 1)
        phase_error = ((ids - expected) / max(phase_radius, 1)) ** 2
        state_cost.append(semantic_weight * semantic_error + phase_weight * phase_error)
        state_sem.append(dst[ids])

    # Pair-state DP: at frame 1 each state is (sample_0, sample_1).
    states = {}
    back = [{} for _ in range(frames)]
    if frames == 1:
        ix = int(np.argmin(state_cost[0]))
        return {"sample_indices": [int(candidates[0][ix])], "total_cost": float(state_cost[0][ix]),
                "transitions": [], "semantic_scale": source_std.tolist()}
    for i_pos, i in enumerate(candidates[0]):
        for j_pos, j in enumerate(candidates[1]):
            step = int(j - i)
            if step < 0 or step > max_step:
                continue
            maximum, rms, _ = pose_pair_distance(poses[i], poses[j])
            if maximum >= max_pose_jump:
                continue
            source_velocity = src[1] - src[0]
            target_velocity = (dst[j] - dst[i])
            vel_error = float(np.mean((target_velocity - source_velocity) ** 2))
            phase_delta = step / max(samples - 1, 1)
            expected_delta = 1.0 / max(frames - 1, 1)
            phase_error = (phase_delta - expected_delta) ** 2
            value = (state_cost[0][i_pos] + state_cost[1][j_pos] +
                     pose_transition_weight * (rms * rms + maximum * maximum) +
                     semantic_velocity_weight * vel_error +
                     phase_velocity_weight * phase_error)
            states[(int(i), int(j))] = value
    if not states:
        raise RuntimeError("no admissible first transition in scaffold")

    for frame in range(2, frames):
        next_states = {}
        predecessor = {}
        source_velocity = src[frame] - src[frame - 1]
        for (i, j), old_cost in states.items():
            for k_pos, k_value in enumerate(candidates[frame]):
                k = int(k_value)
                step = k - j
                if step < 0 or step > max_step:
                    continue
                maximum, rms, _ = pose_pair_distance(poses[j], poses[k])
                if maximum >= max_pose_jump:
                    continue
                j_pos = int(np.searchsorted(candidates[frame - 1], j))
                j_value = state_sem[frame - 1][j_pos]
                k_value_sem = state_sem[frame][k_pos]
                target_velocity = k_value_sem - j_value
                vel_error = float(np.mean((target_velocity - source_velocity) ** 2))
                previous_step = j - i
                accel_error = float((step - previous_step) ** 2)
                phase_delta = step / max(samples - 1, 1)
                expected_delta = 1.0 / max(frames - 1, 1)
                phase_error = (phase_delta - expected_delta) ** 2
                value = (old_cost + state_cost[frame][k_pos] +
                         pose_transition_weight * (rms * rms + maximum * maximum) +
                         semantic_velocity_weight * vel_error +
                         phase_velocity_weight * phase_error +
                         phase_acceleration_weight * accel_error)
                key = (j, k)
                if value < next_states.get(key, math.inf):
                    next_states[key] = value
                    predecessor[key] = (i, j)
        if not next_states:
            raise RuntimeError(f"no admissible scaffold path reaches frame {frame}")
        states = next_states
        back[frame] = predecessor

    state = min(states, key=states.get)
    path = [state[1], state[0]]
    for frame in range(frames - 1, 1, -1):
        state = back[frame][state]
        path.append(state[0])
    path.reverse()
    transitions = []
    for a, b in zip(path, path[1:]):
        maximum, rms, per_bone = pose_pair_distance(poses[a], poses[b])
        transitions.append({"from_sample": int(a), "to_sample": int(b),
                            "phase_step": int(b - a),
                            "max_pose_jump": maximum, "rms_pose_jump": rms,
                            "worst_bone_index": int(np.argmax(per_bone))})
    return {"sample_indices": [int(v) for v in path],
            "total_cost": float(states[min(states, key=states.get)]),
            "transitions": transitions,
            "semantic_scale": source_std.tolist(),
            "candidate_phase_radius": int(phase_radius)}
